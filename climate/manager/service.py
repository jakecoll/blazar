# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime

import eventlet
from oslo.config import cfg
import six
from stevedore import enabled

from climate.db import api as db_api
from climate.db import exceptions as db_ex
from climate import exceptions as common_ex
from climate import manager
from climate import states
from climate.manager import exceptions
from climate.notification import api as notification_api
from climate.openstack.common.gettextutils import _
from climate.openstack.common import log as logging
from climate.utils import service as service_utils
from climate.utils import trusts

manager_opts = [
    cfg.ListOpt('plugins',
                default=['dummy.vm.plugin'],
                help='All plugins to use (one for every resource type to '
                     'support.)'),
    cfg.IntOpt('notify_hours_before_lease_end',
               default=48,
               help='Number of hours prior to lease end in which a '
                    'notification of lease close to expire will be sent. If '
                    'this is set to 0, then this notification will '
                    'not be sent.')
]

CONF = cfg.CONF
CONF.register_opts(manager_opts, 'manager')
LOG = logging.getLogger(__name__)

LEASE_DATE_FORMAT = "%Y-%m-%d %H:%M"


class ManagerService(service_utils.RPCServer):
    """Service class for the climate-manager service.

    Responsible for working with Climate DB, scheduling logic, running events,
    working with plugins, etc.
    """

    def __init__(self):
        target = manager.get_target()
        super(ManagerService, self).__init__(target)
        self.plugins = self._get_plugins()
        self.resource_actions = self._setup_actions()

    def start(self):
        super(ManagerService, self).start()
        self.tg.add_timer(10, self._event)

    def _get_plugins(self):
        """Return dict of resource-plugin class pairs."""
        config_plugins = CONF.manager.plugins
        plugins = {}

        extension_manager = enabled.EnabledExtensionManager(
            check_func=lambda ext: ext.name in config_plugins,
            namespace='climate.resource.plugins',
            invoke_on_load=False
        )

        for ext in extension_manager.extensions:
            try:
                plugin_obj = ext.plugin()
            except Exception as e:
                LOG.warning("Could not load {0} plugin "
                            "for resource type {1} '{2}'".format(
                                ext.name, ext.plugin.resource_type, e))
            else:
                if plugin_obj.resource_type in plugins:
                    msg = ("You have provided several plugins for "
                           "one resource type in configuration file. "
                           "Please set one plugin per resource type.")
                    raise exceptions.PluginConfigurationError(error=msg)

                plugins[plugin_obj.resource_type] = plugin_obj
        return plugins

    def _setup_actions(self):
        """Setup actions for each resource type supported.

        BasePlugin interface provides only on_start and on_end behaviour now.
        If there are some configs needed by plugin, they should be returned
        from get_plugin_opts method. These flags are registered in
        [resource_type] group of configuration file.
        """
        actions = {}

        for resource_type, plugin in six.iteritems(self.plugins):
            plugin = self.plugins[resource_type]
            CONF.register_opts(plugin.get_plugin_opts(), group=resource_type)

            actions[resource_type] = {}
            actions[resource_type]['on_start'] = plugin.on_start
            actions[resource_type]['on_end'] = plugin.on_end
            plugin.setup(None)
        return actions

    @service_utils.with_empty_context
    def _event(self):
        """Tries to commit event.

        If there is an event in Climate DB to be done, do it and change its
        status to 'DONE'.
        """
        LOG.debug('Trying to get event from DB.')
        event = db_api.event_get_first_sorted_by_filters(
            sort_key='time',
            sort_dir='asc',
            filters={'status': 'UNDONE'}
        )

        if not event:
            return

        if event['time'] < datetime.datetime.utcnow():
            db_api.event_update(event['id'], {'status': 'IN_PROGRESS'})
            event_type = event['event_type']
            event_fn = getattr(self, event_type, None)
            if event_fn is None:
                raise exceptions.EventError(error='Event type %s is not '
                                                  'supported' % event_type)
            try:
                eventlet.spawn_n(service_utils.with_empty_context(event_fn),
                                 event['lease_id'], event['id'])
                lease = db_api.lease_get(event['lease_id'])
                with trusts.create_ctx_from_trust(lease['trust_id']) as ctx:
                    self._send_notification(lease,
                                            ctx,
                                            events=['event.%s' % event_type])
            except Exception:
                db_api.event_update(event['id'], {'status': 'ERROR'})
                LOG.exception(_('Error occurred while event handling.'))

    def _date_from_string(self, date_string, date_format=LEASE_DATE_FORMAT):
        try:
            date = datetime.datetime.strptime(date_string, date_format)
        except ValueError:
            raise exceptions.InvalidDate(date=date_string,
                                         date_format=date_format)

        return date

    def get_lease(self, lease_id):
        return db_api.lease_get(lease_id)

    def list_leases(self, project_id=None):
        return db_api.lease_list(project_id)

    def create_lease(self, lease_values):
        """Create a lease with reservations.

        Return either the model of created lease or None if any error.
        """
        try:
            trust_id = lease_values.pop('trust_id')
        except KeyError:
            raise exceptions.MissingTrustId()

        # Remove and keep reservation values
        reservations = lease_values.pop("reservations", [])

        # Create the lease without the reservations
        start_date = lease_values['start_date']
        end_date = lease_values['end_date']

        now = datetime.datetime.utcnow()
        now = datetime.datetime(now.year,
                                now.month,
                                now.day,
                                now.hour,
                                now.minute)
        if start_date == 'now':
            start_date = now
        else:
            start_date = self._date_from_string(start_date)
        end_date = self._date_from_string(end_date)

        if start_date < now:
            raise common_ex.NotAuthorized(
                'Start date must later than current date')

        with trusts.create_ctx_from_trust(trust_id) as ctx:
            lease_values['user_id'] = lease_values['user_id']
            lease_values['project_id'] = ctx.project_id
            lease_values['start_date'] = start_date
            lease_values['end_date'] = end_date

            if not lease_values.get('events'):
                lease_values['events'] = []

            lease_values['events'].append({'event_type': 'start_lease',
                                           'time': start_date,
                                           'status': 'UNDONE'})
            lease_values['events'].append({'event_type': 'end_lease',
                                           'time': end_date,
                                           'status': 'UNDONE'})

            before_end_date = lease_values.get('before_end_notification', None)
            if before_end_date:
                # incoming param. Validation check
                try:
                    before_end_date = self._date_from_string(
                        before_end_date)
                    self._check_date_within_lease_limits(before_end_date,
                                                         lease_values)
                except common_ex.ClimateException as e:
                    LOG.error("Invalid before_end_date param. %s" % e.message)
                    raise e
            elif CONF.manager.notify_hours_before_lease_end > 0:
                delta = datetime.timedelta(
                    hours=CONF.manager.notify_hours_before_lease_end)
                before_end_date = lease_values['end_date'] - delta

            if before_end_date:
                event = {'event_type': 'before_end_lease',
                         'status': 'UNDONE'}
                lease_values['events'].append(event)
                self._update_before_end_event_date(event, before_end_date,
                                                   lease_values)

            try:
                if trust_id:
                    lease_values.update({'trust_id': trust_id})
                lease = db_api.lease_create(lease_values)
                lease_id = lease['id']
            except db_ex.ClimateDBDuplicateEntry:
                LOG.exception('Cannot create a lease - duplicated lease name')
                raise exceptions.LeaseNameAlreadyExists(
                    name=lease_values['name'])
            except db_ex.ClimateDBException:
                LOG.exception('Cannot create a lease')
                raise
            else:
                try:
                    for reservation in reservations:
                        reservation['lease_id'] = lease['id']
                        reservation['start_date'] = lease['start_date']
                        reservation['end_date'] = lease['end_date']
                        resource_type = reservation['resource_type']
                        if resource_type in self.plugins:
                            self.plugins[resource_type].create_reservation(
                                reservation)
                        else:
                            raise exceptions.UnsupportedResourceType(
                                resource_type)
                except (exceptions.UnsupportedResourceType,
                        common_ex.ClimateException):
                    LOG.exception("Failed to create reservation for a lease. "
                                  "Rollback the lease and associated "
                                  "reservations")
                    db_api.lease_destroy(lease_id)
                    raise

                else:
                    lease_state = states.LeaseState(id=lease['id'],
                            action=states.lease.CREATE,
                            status=states.lease.COMPLETE,
                            status_reason="Successfully created lease")
                    lease_state.save()
                    lease = db_api.lease_get(lease['id'])
                    self._send_notification(lease, ctx, events=['create'])
                    return lease

    def update_lease(self, lease_id, values):
        if not values:
            return db_api.lease_get(lease_id)

        if len(values) == 1 and 'name' in values:
            db_api.lease_update(lease_id, values)
            return db_api.lease_get(lease_id)

        lease = db_api.lease_get(lease_id)
        start_date = values.get(
            'start_date',
            datetime.datetime.strftime(lease['start_date'], LEASE_DATE_FORMAT))
        end_date = values.get(
            'end_date',
            datetime.datetime.strftime(lease['end_date'], LEASE_DATE_FORMAT))
        before_end_date = values.get('before_end_notification', None)

        now = datetime.datetime.utcnow()
        now = datetime.datetime(now.year,
                                now.month,
                                now.day,
                                now.hour,
                                now.minute)
        if start_date == 'now':
            start_date = now
        else:
            start_date = self._date_from_string(start_date)
        end_date = self._date_from_string(end_date)

        values['start_date'] = start_date
        values['end_date'] = end_date

        if (lease['start_date'] < now and
                values['start_date'] != lease['start_date']):
            raise common_ex.NotAuthorized(
                'Cannot modify the start date of already started leases')

        if (lease['start_date'] > now and
                values['start_date'] < now):
            raise common_ex.NotAuthorized(
                'Start date must later than current date')

        if lease['end_date'] < now:
            raise common_ex.NotAuthorized(
                'Terminated leases can only be renamed')

        if (values['end_date'] < now or
           values['end_date'] < values['start_date']):
            raise common_ex.NotAuthorized(
                'End date must be later than current and start date')

        with trusts.create_ctx_from_trust(lease['trust_id']):
            if before_end_date:
                try:
                    before_end_date = self._date_from_string(before_end_date)
                    self._check_date_within_lease_limits(before_end_date,
                                                         values)
                except common_ex.ClimateException as e:
                    LOG.error("Invalid before_end_date param. %s" % e.message)
                    raise e

            # TODO(frossigneux) rollback if an exception is raised
            for reservation in (
                    db_api.reservation_get_all_by_lease_id(lease_id)):
                reservation['start_date'] = values['start_date']
                reservation['end_date'] = values['end_date']
                resource_type = reservation['resource_type']
                self.plugins[resource_type].update_reservation(
                    reservation['id'],
                    reservation)

        event = db_api.event_get_first_sorted_by_filters(
            'lease_id',
            'asc',
            {
                'lease_id': lease_id,
                'event_type': 'start_lease'
            }
        )
        if not event:
            raise common_ex.ClimateException(
                'Start lease event not found')
        db_api.event_update(event['id'], {'time': values['start_date']})

        event = db_api.event_get_first_sorted_by_filters(
            'lease_id',
            'asc',
            {
                'lease_id': lease_id,
                'event_type': 'end_lease'
            }
        )
        if not event:
            raise common_ex.ClimateException(
                'End lease event not found')
        db_api.event_update(event['id'], {'time': values['end_date']})

        notifications = ['update']
        self._update_before_end_event(lease, values, notifications,
                                      before_end_date)

        db_api.lease_update(lease_id, values)

        lease_state = states.LeaseState(id=lease_id,
                action=states.lease.UPDATE,
                status=states.lease.COMPLETE,
                status_reason="Successfully updated lease")
        lease_state.save()
        lease = db_api.lease_get(lease_id)
        with trusts.create_ctx_from_trust(lease['trust_id']) as ctx:
            self._send_notification(lease, ctx, events=notifications)

        return lease

    def delete_lease(self, lease_id):
        lease = self.get_lease(lease_id)
        if (datetime.datetime.utcnow() < lease['start_date'] or
                datetime.datetime.utcnow() > lease['end_date']):
            with trusts.create_ctx_from_trust(lease['trust_id']) as ctx:
                for reservation in lease['reservations']:
                    plugin = self.plugins[reservation['resource_type']]
                    try:
                        plugin.on_end(reservation['resource_id'])
                    except (db_ex.ClimateDBException, RuntimeError):
                        error_msg = "Failed to delete a reservation for a lease."
                        lease_state = states.LeaseState(id=lease_id,
                                action=states.lease.DELETE,
                                status=states.lease.FAILED,
                                status_reason=error_msg)
                        lease_state.save()
                        LOG.exception(error_msg)
                        raise
                db_api.lease_destroy(lease_id)
                self._send_notification(lease, ctx, events=['delete'])
        else:
            error_msg = "Already started lease cannot be deleted"
            lease_state = states.LeaseState(id=lease_id,
                    action=states.lease.DELETE,
                    status=states.lease.FAILED,
                    status_reason=error_msg)
            lease_state.save()
            raise common_ex.NotAuthorized(error_msg)

    def start_lease(self, lease_id, event_id):
        lease = self.get_lease(lease_id)
        with trusts.create_ctx_from_trust(lease['trust_id']):
            self._basic_action(lease_id, event_id, 'on_start', 'active')

    def end_lease(self, lease_id, event_id):
        lease = self.get_lease(lease_id)
        with trusts.create_ctx_from_trust(lease['trust_id']):
            self._basic_action(lease_id, event_id, 'on_end', 'deleted')

    def before_end_lease(self, lease_id, event_id):
        db_api.event_update(event_id, {'status': 'DONE'})

    def _basic_action(self, lease_id, event_id, action_time,
                      reservation_status=None):
        """Commits basic lease actions such as starting and ending."""
        lease = self.get_lease(lease_id)

        event_status = 'DONE'

        if action_time == 'on_start':
            lease_action = states.lease.START
            status_reason = "Starting lease..."
        elif action_time == 'on_end':
            lease_action = states.lease.STOP
            status_reason = "Stopping lease..."
        else:
            raise AttributeError("action_time is %s instead of either on_start or on_end"
                                 % action_time)

        lease_state = states.LeaseState(id=lease_id, action=lease_action,
                status=states.lease.IN_PROGRESS,
                status_reason=status_reason)
        lease_state.save()

        for reservation in lease['reservations']:
            resource_type = reservation['resource_type']
            try:
                self.resource_actions[resource_type][action_time](
                    reservation['resource_id']
                )
            except common_ex.ClimateException:
                LOG.exception("Failed to execute action %(action)s "
                              "for lease %(lease)s"
                              % {
                                  'action': action_time,
                                  'lease': lease_id,
                              })
                event_status = 'ERROR'
                db_api.reservation_update(reservation['id'],
                                          {'status': 'error'})
            else:
                if reservation_status is not None:
                    db_api.reservation_update(reservation['id'],
                                              {'status': reservation_status})

        db_api.event_update(event_id, {'status': event_status})

        if event_status == 'DONE':
            lease_status = states.lease.COMPLETE
            if action_time ==  'on_start':
                status_reason = "Successfully started lease"
            elif action_time == 'on_end':
                status_reason = "Successfully stopped lease"
            else:
                raise AttributeError("action_time is %s instead of either on_start or on_end"
                                     % action_time)
        elif event_status == 'ERROR':
            lease_status = states.lease.FAILED
            if action_time ==  'on_start':
                status_reason = "Failed to start lease"
            elif action_time == 'on_end':
                status_reason = "Failed to stop lease"
            else:
                raise AttributeError("action_time is %s instead of either on_start or on_end"
                                     % action_time)
        else:
            raise AttributeError("event_status is %s instead of either DONE or ERROR"
                                 % event_status)

        lease_state.update(action=lease_action,
                           status=lease_status,
                           status_reason=status_reason)
        lease_state.save()

    def _send_notification(self, lease, ctx, events=[]):
        payload = notification_api.format_lease_payload(lease)

        for event in events:
            notification_api.send_lease_notification(ctx, payload,
                                                     'lease.%s' % event)

    def _check_date_within_lease_limits(self, date, lease):
        if not lease['start_date'] < date < lease['end_date']:
            raise common_ex.NotAuthorized(
                'Datetime is out of lease limits')

    def _update_before_end_event_date(self, event, before_end_date, lease):
        event['time'] = before_end_date
        if event['time'] < lease['start_date']:
            LOG.warning("New start_date greater than before_end_date. "
                        "Setting before_end_date to %s for lease %s"
                        % (lease['start_date'], lease.get('id',
                           lease.get('name'))))
            event['time'] = lease['start_date']

    def _update_before_end_event(self, old_lease, new_lease,
                                 notifications, before_end_date=None):
        event = db_api.event_get_first_sorted_by_filters(
            'lease_id',
            'asc',
            {
                'lease_id': old_lease['id'],
                'event_type': 'before_end_lease'
            }
        )
        if event:
            # NOTE(casanch1) do nothing if the event does not exist.
            # This is for backward compatibility
            update_values = {}
            if not before_end_date:
                # before_end_date needs to be calculated based on
                # previous delta
                prev_before_end_delta = old_lease['end_date'] - event['time']
                before_end_date = new_lease['end_date'] - prev_before_end_delta

            self._update_before_end_event_date(update_values, before_end_date,
                                               new_lease)
            if event['status'] == 'DONE':
                update_values['status'] = 'UNDONE'
                notifications.append('event.before_end_lease.stop')

            db_api.event_update(event['id'], update_values)

    def __getattr__(self, name):
        """RPC Dispatcher for plugins methods."""

        fn = None
        try:
            resource_type, method = name.rsplit(':', 1)
        except ValueError:
            # NOTE(sbauza) : the dispatcher needs to know which plugin to use,
            #  raising error if consequently not
            raise AttributeError(name)
        try:
            try:
                fn = getattr(self.plugins[resource_type], method)
            except KeyError:
                LOG.error("Plugin with resource type %s not found",
                          resource_type)
                raise exceptions.UnsupportedResourceType(resource_type)
        except AttributeError:
            LOG.error("Plugin %s doesn't include method %s",
                      self.plugins[resource_type], method)
        if fn is not None:
            return fn
        raise AttributeError(name)
