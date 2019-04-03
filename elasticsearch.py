﻿#!/usr/bin/python
# -*- coding: utf-8 -*-
#
#The MIT License (MIT)
#
#Copyright (c) 2015 Engin Yöyen
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:
#
#The above copyright notice and this permission notice shall be included in all
#copies or substantial portions of the Software.
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#SOFTWARE.

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import json
import logging
import pytz
import time
import json


from datetime import datetime
from ansible import constants as C
from ansible.plugins.callback import CallbackBase



class CallbackModule(CallbackBase):
    """
    This is a Ansible(v2) elasticsearch callback plugin.
    It collects the result of ansible task runs and ships it to the 
    elasticsearch.
    
    This plugin makes use of the following environment variables:
        ELASTICSEARCH_SERVER   (optional): defaults to localhost
        ELASTICSEARCH_PORT     (optional): defaults to 9200
        ELASTICSEARCH_TIMEOUT  (optional): defaults to 3 (seconds)
        ELASTICSEARCH_INDEX    (optional): defaults to 3 ansible_logs
        ELASTICSEARCH_DOC_ARGS (optional): Addtional json key-value pair(e.g. {"bar":"abc", "foo":"def"}) to be stored in each document
    """
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'elasticsearch'
    CALLBACK_NEEDS_WHITELIST = False

    def __init__(self):

        super(CallbackModule, self).__init__()

        try:
            self.elasticsearch = __import__('elasticsearch')
            self.helpers = __import__('elasticsearch.helpers')
            self.db_import = True
        except ImportError:
            self.db_import = False
            logging.error("Failed to import elasticsearch module. Maybe you can use pip to install!")
        
        #Optional environment variables
        self.elasticsearch_host = os.getenv('ELASTICSEARCH_SERVER','localhost')
        self.elasticsearch_port = os.getenv('ELASTICSEARCH_PORT', 9200)
        self.args = os.getenv('ELASTICSEARCH_DOC_ARGS')
        if self.args is not None:
            self.args = json.loads(self.args)
        self.timeout = os.getenv('ELASTICSEARCH_TIMEOUT', 3)
        self.index_name = os.getenv('ELASTICSEARCH_INDEX', "ansible_logs") + "-"+ time.strftime('%Y.%m.%d')  # ES index name one per day

        #Elasticsearch variables
        self.es_address = self.elasticsearch_host + ":" + str(self.elasticsearch_port)
        self.es_status = self._connect()

        #Log variables
        self.run_output = []
        self.taskname = ""
        self.playname = ""  


        self.logger =  logging.getLogger('ansible logger')
        self.logger.setLevel(logging.ERROR)


    def _connect(self):
        if self.db_import:
            try:
                self.es = self.elasticsearch.Elasticsearch(self.es_address, timeout=self.timeout)
            except Exception as e:
                logging.error("Failed to connect elasticsearch server '%s'. Exception = %s " % (self.es_address, e))
                return False
            try:
                return self.es.ping()
            except Exception as e:
                logging.error("Failed to get ping from elasticsearch server '%s'. Exception = %s " % (self.es_address, e))
                return False

    def _getTime(self):
        return datetime.utcnow().replace(tzinfo=pytz.utc)

    def _insert(self):
        if self.es_status:
            try:
                result = self.helpers.helpers.bulk(self.es, self.run_output,index=self.index_name)
                if result:
                    return True
            except Exception as e:
                logging.error("Inserting data into elasticsearch 'failed' because %s" % e)
        return False


    def process_data(self, status, hostname,other=None, doc_type="ansible-runs"):
         results = {}
         results['hostname'] = hostname
         results['play'] = self.playname
         results['task'] = self.taskname
         results['status'] = status
         results['timestamp'] =  self._getTime()
         results['_type'] = doc_type
         if self.args is not None:
            results.update(self.args)
         self.run_output.append(results)

    def v2_runner_on_ok(self, result):
        status = None
        delegated_vars = result._result.get('_ansible_delegated_vars', None)
        if result._task.action == 'include':
            return
        elif result._result.get('changed', False):
            status =  "Changed"
        else:
            status =  "Ok"

        if result._task.loop and 'results' in result._result:
            self._process_items(result)

        self.process_data(status, result._host.get_name())
    
    def v2_runner_on_failed(self, result, ignore_errors=False):
        results = {}
        results['exception'] = result._host.get_name()
        if result._task.ignore_errors:
            results['ignore_errors'] = "yes"
        if 'exception' in result._result:
            error = result._result['exception'].strip().split('\n')[-1]
            results['error'] = error
        self.process_data("Failed", result._host.get_name(),results,"ansible-failures")

    def v2_runner_on_unreachable(self, result):
        self.process_data("Unreachable", result._host.get_name())


    def v2_playbook_on_task_start(self, task, is_conditional):
        self.taskname = task.get_name().strip()

    def v2_playbook_on_play_start(self, play):
        self.playname = play.get_name().strip()


    def v2_runner_on_skipped(self, result):
        if C.DISPLAY_SKIPPED_HOSTS:
            if result._task.loop and 'results' in result._result:
                self._process_items(result)
            else:
                self.process_data("Skipped", result._host.get_name())

    def v2_playbook_on_stats(self, stats):
        hosts = sorted(stats.processed.keys())
        for h in hosts:
            t = stats.summarize(h)
            results = {}
            results['hostname'] = h
            results['ok'] = t['ok']
            results['changed'] = t['changed']
            results['failed'] = t['failures']
            results['unreachable'] = t['unreachable']
            results['_type'] = "ansible-stats"
            if self.args is not None:
                results.update(self.args)
            self.run_output.append(results)
        self._insert()


