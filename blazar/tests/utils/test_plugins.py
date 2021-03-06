# Copyright (c) 2017 NTT.
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


from blazar.manager import exceptions as manager_exceptions
from blazar import tests
from blazar.utils import plugins as plugins_utils


class TestPluginsUtils(tests.TestCase):

    def setUp(self):
        super(TestPluginsUtils, self).setUp()

    def test_convert_requirements_empty(self):
        request = '[]'
        result = plugins_utils.convert_requirements(request)
        self.assertEqual([], result)

    def test_convert_requirements_small(self):
        request = '["=", "$memory", "4096"]'
        result = plugins_utils.convert_requirements(request)
        self.assertEqual(['memory == 4096'], result)

    def test_convert_requirements_with_incorrect_syntax_1(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements, '["a", "$memory", "4096"]')

    def test_convert_requirements_with_incorrect_syntax_2(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements, '["=", "memory", "4096"]')

    def test_convert_requirements_with_incorrect_syntax_3(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements, '["=", "$memory", 4096]')

    def test_convert_requirements_complex(self):
        request = '["and", [">", "$memory", "4096"], [">", "$disk", "40"]]'
        result = plugins_utils.convert_requirements(request)
        self.assertEqual(['memory > 4096', 'disk > 40'], result)

    def test_convert_requirements_complex_with_incorrect_syntax_1(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements,
            '["and", [">", "memory", "4096"], [">", "$disk", "40"]]')

    def test_convert_requirements_complex_with_incorrect_syntax_2(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements,
            '["fail", [">", "$memory", "4096"], [">", "$disk", "40"]]')

    def test_convert_requirements_complex_with_not_json_value(self):
        self.assertRaises(
            manager_exceptions.MalformedRequirements,
            plugins_utils.convert_requirements, 'something')
