# -*- coding: utf-8 -*-
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import sys
import unittest
from io import StringIO

import mock

from airflow import configuration, models
from airflow.utils import db
from airflow.contrib.hooks.spark_submit_hook import SparkSubmitHook


class TestSparkSubmitHook(unittest.TestCase):

    _spark_job_file = 'test_application.py'
    _config = {
        'conf': {
            'parquet.compression': 'SNAPPY'
        },
        'conn_id': 'default_spark',
        'files': 'hive-site.xml',
        'py_files': 'sample_library.py',
        'jars': 'parquet.jar',
        'executor_cores': 4,
        'executor_memory': '22g',
        'keytab': 'privileged_user.keytab',
        'principal': 'user/spark@airflow.org',
        'name': 'spark-job',
        'num_executors': 10,
        'verbose': True,
        'driver_memory': '3g',
        'java_class': 'com.foo.bar.AppMain'
    }

    def setUp(self):

        if sys.version_info[0] == 3:
            raise unittest.SkipTest('TestSparkSubmitHook won\'t work with '
                                    'python3. No need to test anything here')

        configuration.load_test_config()
        db.merge_conn(
            models.Connection(
                conn_id='spark_yarn_cluster', conn_type='spark',
                host='yarn://yarn-master', extra='{"queue": "root.etl", "deploy-mode": "cluster"}')
        )
        db.merge_conn(
            models.Connection(
                conn_id='spark_default_mesos', conn_type='spark',
                host='mesos://host', port=5050)
        )

        db.merge_conn(
            models.Connection(
                conn_id='spark_home_set', conn_type='spark',
                host='yarn://yarn-master',
                extra='{"spark-home": "/opt/myspark"}')
        )

        db.merge_conn(
            models.Connection(
                conn_id='spark_home_not_set', conn_type='spark',
                host='yarn://yarn-master')
        )

    def test_build_command(self):
        hook = SparkSubmitHook(**self._config)

        # The subprocess requires an array but we build the cmd by joining on a space
        cmd = ' '.join(hook._build_command(self._spark_job_file))

        # Check if the URL gets build properly and everything exists.
        assert self._spark_job_file in cmd

        # Check all the parameters
        assert "--files {}".format(self._config['files']) in cmd
        assert "--py-files {}".format(self._config['py_files']) in cmd
        assert "--jars {}".format(self._config['jars']) in cmd
        assert "--executor-cores {}".format(self._config['executor_cores']) in cmd
        assert "--executor-memory {}".format(self._config['executor_memory']) in cmd
        assert "--keytab {}".format(self._config['keytab']) in cmd
        assert "--principal {}".format(self._config['principal']) in cmd
        assert "--name {}".format(self._config['name']) in cmd
        assert "--num-executors {}".format(self._config['num_executors']) in cmd
        assert "--class {}".format(self._config['java_class']) in cmd
        assert "--driver-memory {}".format(self._config['driver_memory']) in cmd

        # Check if all config settings are there
        for k in self._config['conf']:
            assert "--conf {0}={1}".format(k, self._config['conf'][k]) in cmd

        if self._config['verbose']:
            assert "--verbose" in cmd

    @mock.patch('airflow.contrib.hooks.spark_submit_hook.subprocess')
    def test_submit(self, mock_process):
        # We don't have spark-submit available, and this is hard to mock, so let's
        # just use this simple mock.
        mock_Popen = mock_process.Popen.return_value
        mock_Popen.stdout = StringIO(u'stdout')
        mock_Popen.stderr = StringIO(u'stderr')
        mock_Popen.returncode = None
        mock_Popen.communicate.return_value = ['extra stdout', 'extra stderr']
        hook = SparkSubmitHook()
        hook.submit(self._spark_job_file)

    def test_resolve_connection(self):

        # Default to the standard yarn connection because conn_id does not exists
        hook = SparkSubmitHook(conn_id='')
        self.assertEqual(hook._resolve_connection(), ('yarn', None, None, None))
        assert "--master yarn" in ' '.join(hook._build_command(self._spark_job_file))

        # Default to the standard yarn connection
        hook = SparkSubmitHook(conn_id='spark_default')
        self.assertEqual(
            hook._resolve_connection(),
            ('yarn', 'root.default', None, None)
        )
        cmd = ' '.join(hook._build_command(self._spark_job_file))
        assert "--master yarn" in cmd
        assert "--queue root.default" in cmd

        # Connect to a mesos master
        hook = SparkSubmitHook(conn_id='spark_default_mesos')
        self.assertEqual(
            hook._resolve_connection(),
            ('mesos://host:5050', None, None, None)
        )

        cmd = ' '.join(hook._build_command(self._spark_job_file))
        assert "--master mesos://host:5050" in cmd

        # Set specific queue and deploy mode
        hook = SparkSubmitHook(conn_id='spark_yarn_cluster')
        self.assertEqual(
            hook._resolve_connection(),
            ('yarn://yarn-master', 'root.etl', 'cluster', None)
        )

        cmd = ' '.join(hook._build_command(self._spark_job_file))
        assert "--master yarn://yarn-master" in cmd
        assert "--queue root.etl" in cmd
        assert "--deploy-mode cluster" in cmd

        # Set the spark home
        hook = SparkSubmitHook(conn_id='spark_home_set')
        self.assertEqual(
            hook._resolve_connection(),
            ('yarn://yarn-master', None, None, '/opt/myspark')
        )

        cmd = ' '.join(hook._build_command(self._spark_job_file))
        assert cmd.startswith('/opt/myspark/bin/spark-submit')

        # Spark home not set
        hook = SparkSubmitHook(conn_id='spark_home_not_set')
        self.assertEqual(
            hook._resolve_connection(),
            ('yarn://yarn-master', None, None, None)
        )

        cmd = ' '.join(hook._build_command(self._spark_job_file))
        assert cmd.startswith('spark-submit')

    def test_process_log(self):
        # Must select yarn connection
        hook = SparkSubmitHook(conn_id='spark_yarn_cluster')

        log_lines = [
            'SPARK_MAJOR_VERSION is set to 2, using Spark2',
            'WARN NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable',
            'WARN DomainSocketFactory: The short-circuit local reads feature cannot be used because libhadoop cannot be loaded.',
            'INFO Client: Requesting a new application from cluster with 10 NodeManagers',
            'INFO Client: Submitting application application_1486558679801_1820 to ResourceManager'
        ]

        hook._process_log(log_lines)

        assert hook._yarn_application_id == 'application_1486558679801_1820'


if __name__ == '__main__':
    unittest.main()
