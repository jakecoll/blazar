# Copyright (c) 2013 Bull.
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

from climate import exceptions
from climate.openstack.common.gettextutils import _


class NoFreePool(exceptions.NotFound):
    msg_fmt = _("No Freepool found")


class HostNotInFreePool(exceptions.NotFound):
    msg_fmt = _("Host %(host)s not in freepool '%(freepool_name)s'")


class CantRemoveHost(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Can't remove host(s) %(host)s from Aggregate %(pool)s")


class CantAddHost(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Can't add host(s) %(host)s to Aggregate %(pool)s")


class AggregateHaveHost(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Can't delete Aggregate '%(name)s', "
                "host(s) attached to it : %(hosts)s")


class AggregateAlreadyHasHost(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Aggregate %(pool)s already has host(s) %(host)s ")


class AggregateNotFound(exceptions.NotFound):
    msg_fmt = _("Aggregate '%(pool)s' not found!")


class HostNotFound(exceptions.NotFound):
    msg_fmt = _("Host '%(host)s' not found!")


class InvalidHost(exceptions.NotAuthorized):
    msg_fmt = _("Invalid values for host %(host)s")


class MultipleHostsFound(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Multiple Hosts found for pattern '%(host)s'")


class HostHavingServers(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Servers [%(servers)s] found for host %(host)s")


class ConfigurationError(exceptions.ClimateException):
    msg_fmt = _("Configuration error : %(error)s")


class PluginConfigurationError(exceptions.ClimateException):
    msg_fmt = _("Plugin Configuration error : %(error)s")


class EventError(exceptions.ClimateException):
    msg_fmt = '%(error)s'


class InvalidDate(exceptions.ClimateException):
    msg_fmt = _(
        '%(date)s is an invalid date. Required format: %(date_format)s')


class UnsupportedResourceType(exceptions.ClimateException):
    msg_fmt = _("The %(resource_type)s resource type is not supported")


class LeaseNameAlreadyExists(exceptions.ClimateException):
    code = 409
    msg_fmt = _("The lease with name: %(name)s already exists")


class MissingTrustId(exceptions.ClimateException):
    msg_fmt = _("A trust id is required")


# oshost plugin related exceptions

class CantAddExtraCapability(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Can't add extracapabilities %(keys)s to Host %(host)s")


class EndpointsNotFound(exceptions.NotFound):
    code = 404
    msg_fmt = _("No endpoints for %(service)s")


class ServiceNotFound(exceptions.NotFound):
    code = 404
    msg_fmt = _("Service %(service)s not found")


class WrongClientVersion(exceptions.ClimateException):
    code = 400
    msg_fmt = _("Unfortunately you use wrong client version")


class NoManagementUrl(exceptions.NotFound):
    code = 404
    msg_fmt = _("You haven't management url for service")


class HypervisorNotFound(exceptions.ClimateException):
    msg_fmt = _("Aggregate '%(pool)s' not found!")


class NotEnoughHostsAvailable(exceptions.ClimateException):
    msg_fmt = _("Not enough hosts available")


class MalformedRequirements(exceptions.ClimateException):
    code = 400
    msg_fmt = _("Malformed requirements %(rqrms)s")


class InvalidState(exceptions.ClimateException):
    code = 409
    msg_fmt = _("Invalid State %(state)s for %(id)s")


class InvalidStateUpdate(InvalidState):
    msg_fmt = _("Unable to update ID %(id)s state with %(action)s:%(status)s")


class ProjectIdNotFound(exceptions.ClimateException):
    msg_fmt = _("No project_id found in current context")


class MalformedParameter(exceptions.ClimateException):
    code = 400
    msg_fmt = _("Malformed parameter %(param)s")


class MissingParameter(exceptions.ClimateException):
    code = 400
    msg_fmt = _("Missing parameter %(param)s")
