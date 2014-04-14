# Copyright 2014 Intel Corporation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
import dateutil.parser
import json

from novaclient import exceptions as nova_exceptions

from tempest.common.utils import test_utils
from tempest import config
from tempest import exceptions
from tempest.openstack.common import log as logging
from tempest.scenario import resource_reservation_scenario as rrs
from tempest import test

CONF = config.CONF

LOG = logging.getLogger(__name__)

# same as the one at climate/manager/service
LEASE_DATE_FORMAT = "%Y-%m-%d %H:%M"
LEASE_MIN_DURATION = 2
#TODO(cmart): LEASE_IMAGE_PREFIX should be extracted from CONF
LEASE_IMAGE_PREFIX = 'reserved_'


class TestResourceReservationScenario(rrs.ResourceReservationScenarioTest):

    """Test that checks the resource reservation scenario.

    The following is the scenario outline:
    1) Create an instance with the hint parameters
    2) check vm was shelved
    3) check vm became active
    4) check that a new lease is created on climate
    5) check its param
    6) wait lease end
    7) make sure VM was snapshoted and removed

    """

    def setUp(self):
        super(TestResourceReservationScenario, self).setUp()
        # Setup image and flavor the test instance
        # Support both configured and injected values
        if not hasattr(self, 'image_ref'):
            self.image_ref = CONF.compute.image_ref
        if not hasattr(self, 'flavor_ref'):
            self.flavor_ref = CONF.compute.flavor_ref
        self.image_utils = test_utils.ImageUtils()
        if not self.image_utils.is_flavor_enough(self.flavor_ref,
                                                 self.image_ref):
            raise self.skipException(
                '{image} does not fit in {flavor}'.format(
                    image=self.image_ref, flavor=self.flavor_ref
                )
            )

    def tearDown(self):
        self.remove_resource('server')
        super(TestResourceReservationScenario, self).tearDown()

    def add_keypair(self):
        self.keypair = self.create_keypair()

    def boot_server_with_lease_data(self, lease_data, wait):
        self.add_keypair()

        # Create server with lease_data
        create_kwargs = {
            'key_name': self.keypair.id,
            'scheduler_hints': lease_data
        }

        server = self.create_server(image=self.image_ref,
                                    flavor=self.flavor_ref, wait=wait,
                                    create_kwargs=create_kwargs)
        self.set_resource('server', server)

    def check_lease_creation(self, expected_lease_data):
        server = self.get_resource('server')
        expected_lease_params = json.loads(expected_lease_data['lease_params'])

        # compare lease_data with data passed as parameter
        lease = self.get_lease_by_name(expected_lease_params['name'])
        self.assertNotEmpty(lease)

        # check lease dates!! (Beware of date format)
        lease_start_date = dateutil.parser.parse(lease['start_date'])
        lease_start_date = lease_start_date.strftime(LEASE_DATE_FORMAT)
        lease_end_date = dateutil.parser.parse(lease['end_date'])
        lease_end_date = lease_end_date.strftime(LEASE_DATE_FORMAT)

        self.assertEqual(expected_lease_params['start'], lease_start_date)
        self.assertEqual(expected_lease_params['end'], lease_end_date)

        #check lease events!
        events = lease['events']
        self.assertTrue(len(events) == 2)

        self.assertFalse(
            len(filter(lambda evt: evt['event_type'] != 'start_lease' and
                       evt['event_type'] != 'end_lease',
                       events)) > 0)

        # check that only one reservation was made and it is for a vm
        # compare the resource id from the lease with the server.id attribute!
        reservations = lease['reservations']
        self.assertTrue(len(reservations) == 1)
        self.assertEqual(server.id, reservations[0]['resource_id'])
        self.assertEqual("virtual:instance",
                         lease['reservations'][0]['resource_type'])

    def check_server_is_snapshoted(self):
        image_name = LEASE_IMAGE_PREFIX + self.get_resource('server').name
        try:
            images_list = self.compute_client.images.list()
            self.assertNotEmpty(
                filter(lambda image: image.name == image_name, images_list))
        except Exception as e:
            message = "Unable to find image with name '%s'. " \
                      "Exception: %s" % (image_name, e.message)
            raise exceptions.NotFound(message)

    def check_server_is_removed(self):
        server_id = self.get_resource('server').id
        self.assertRaises(nova_exceptions.NotFound,
                          self.compute_client.servers.get,
                          server_id)

    def check_server_status(self, expected_status):
        server_id = self.get_resource('server').id
        server = self.compute_client.servers.get(server_id)
        self.assertEqual(expected_status, server.status)

        #update server resource reference
        self.set_resource('server', server)

    def wait_for_server_status(self, status):
        self.status_timeout(
            self.compute_client.servers,
            self.get_resource('server').id, status)
        self.check_server_status(status)

    #TODO(cmart): add climate to services after pushing this code into tempest
    @test.attr(type='slow')
    @test.services('compute', 'network')
    def test_server_basic_resource_reservation_operation(self):
        start_date = datetime.datetime.utcnow() + datetime.timedelta(minutes=1)
        end_date = start_date + datetime.timedelta(minutes=LEASE_MIN_DURATION)
        start_date = start_date.strftime(LEASE_DATE_FORMAT)
        end_date = end_date.strftime(LEASE_DATE_FORMAT)
        lease_name = 'scenario_test'
        lease_data = {
            'lease_params': '{"name": "%s",'
                            '"start": "%s",'
                            '"end": "%s"}'
                            % (lease_name, start_date, end_date)}

        # boot the server and don't wait until it is active
        self.boot_server_with_lease_data(lease_data, wait=False)
        self.check_server_status('SHELVED_OFFLOADED')

        #now, wait until the server is active
        self.wait_for_server_status('ACTIVE')
        self.check_lease_creation(lease_data)

        # wait for lease end
        self.wait_for_lease_end(self.get_lease_by_name(lease_name)['id'])

        # check server final status
        self.check_server_is_snapshoted()
        self.check_server_is_removed()

        #remove created snapshot
        image_name = LEASE_IMAGE_PREFIX + self.get_resource('server').name
        self.remove_image_snapshot(image_name)
