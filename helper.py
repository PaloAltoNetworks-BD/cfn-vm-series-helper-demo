import sys
import os
import os.path
import time
import logging
import json
import urllib2
import uuid

import ansible.playbook
import ansible.callbacks
import boto.sqs
import boto.sqs.queue
import boto.ec2
import jinja2
import datetime

LOG = logging.getLogger(__name__)
EVENTS = None

class WebEvents(object):
    def __init__(self, template, target):
        self.target = target
        self.events = []
        self._setup_template(template)

    def add_waiting(self):
        if len(self.events) > 0 and self.events[-1]['status'] == 'progress':
            self.events[-1]['status'] = 'ok'
        self.events.append({ 'time': datetime.datetime.utcnow().strftime('%Y-%m-%d %H-%M-%S'), 'title': 'waiting', 'status': 'progress', 'result': None })
        self._render()

    def add_pb_event(self, title):
        if len(self.events) > 0 and self.events[-1]['status'] == 'progress':
            self.events[-1]['status'] = 'ok'
        self.events.append({ 'time': datetime.datetime.utcnow().strftime('%Y-%m-%d %H-%M-%S'), 'title': title, 'status': 'progress', 'result': None })
        self._render()

    def set_result(self, status, result):
        if len(self.events) == 0:
            LOG.error("no event for set_result")
            return
        self.events[-1]['status'] = status
        self.events[-1]['result'] = result
        self._render()

    def _setup_template(self, template):
        f = open(template, 'r')
        tpl = f.read()
        f.close()
        self.j2template = jinja2.Template(tpl)

    def _render(self):
        ts = self.j2template.stream(events=self.events)
        f = open(self.target, 'wb+')
        ts.dump(f)

class HelperPlaybookCallbacks(object):
    def __init__(self):
        pass

    def on_start(self):
        LOG.debug("on_start")

    def on_notify(self, host, handler):
        LOG.debug("on_notify")

    def on_no_hosts_matched(self):
        LOG.error("on_no_hosts_matched")

    def on_no_hosts_remaining(self):
        LOG.critical("on_no_hosts_remaining")

    def on_task_start(self, name, is_conditional):
        EVENTS.add_pb_event(name)
        LOG.info("TASK: [%s] - is_conditional: %s", name, is_conditional)

    def on_vars_prompt(self, varname, private=True, prompt=None, encrypt=None, confirm=False, salt_size=None, salt=None, default=None):
        LOG.critical("on_vars_prompt - we should not be here")
        return None

    def on_setup(self):
        EVENTS.add_pb_event('setup')
        LOG.debug("on_setup")

    def on_import_for_host(self, host, imported_file):
        LOG.debug("on_import_for_host")

    def on_not_import_for_host(self, host, missing_file):
        LOG.debug("on_not_import_for_host")

    def on_play_start(self, name):
        EVENTS.add_pb_event('starting playbook {%s}'%name)
        LOG.info("PLAY[%s]", name)

    def on_stats(self, stats):
        LOG.debug("on_stats")

class HelperRunnerCallbacks(ansible.callbacks.DefaultRunnerCallbacks):
    def __init__(self):
        pass

    def on_failed(self, host, res, ignore_errors):
        EVENTS.set_result('failed', json.dumps(res))
        LOG.error("FAILED: %s %s %s", host, json.dumps(res), ignore_errors)
        super(HelperRunnerCallbacks, self).on_failed(host, res, ignore_errors)

    def on_ok(self, host, res):
        EVENTS.set_result('ok', json.dumps(res))
        LOG.info("OK: %s %s", host, json.dumps(res))
        super(HelperRunnerCallbacks, self).on_ok(host, res)

    def on_skipped(self, host, item=None):
        EVENTS.set_result('skipped', json.dumps(res))
        LOG.info("SKIPPED: %s %s", host, item)
        super(HelperRunnerCallbacks, self).on_skipped(host, item)

    def on_unreachable(self, host, res):
        LOG.error("UNREACHABLE: %s %s", host, json.dumps(res))
        super(HelperRunnerCallbacks, self).on_unreachable(host, res)

    def on_no_hosts(self):
        LOG.error("NOHOSTS")
        super(HelperRunnerCallbacks, self).on_no_hosts()

    def on_async_poll(self, host, res, jid, clock):
        LOG.info("ASYNCPOLL: %s %s %s %s", host, json.dumps(res), jid, clock)
        super(HelperRunnerCallbacks, self).on_async_poll(host, res, jid, clock)

    def on_async_ok(self, host, res, jid):
        LOG.info("ASYNCOK: %s %s %s", host, json.dumps(res), jid)
        super(HelperRunnerCallbacks, self).on_async_ok(host, res, jid)

    def on_async_failed(self, host, res, jid):
        LOG.error("ASYNCFAILED: %s %s %s", host, json.dumps(res), jid)
        super(HelperRunnerCallbacks, self).on_async_failed(host, res, jid)

    def on_file_diff(self, host, diff):
        LOG.info("FILEDIFF: %s %s", host, diff)
        super(HelperRunnerCallbacks, self).on_file_diff(host, diff)

def reply_to_msg(m, success=True, reason="OK", data=None):
    LOG.debug("url: %s", m['ResponseURL'])

    source_attributes = {
        "Status": "SUCCESS" if success else "FAILED",
        "StackId": m["StackId"],
        "RequestId": m["RequestId"],
        "LogicalResourceId": m["LogicalResourceId"]
    }
    if not success:
        source_attributes['Reason'] = reason

    if 'PhysicalResourceId' in m:
        source_attributes['PhysicalResourceId'] = m['PhysicalResourceId']
    else:
        source_attributes['PhysicalResourceId'] = str(uuid.uuid4())

    if data is not None:
        source_attributes['Data'] = data

    LOG.debug("response data: %s", json.dumps(source_attributes))

    try:
        r = urllib2.Request(m['ResponseURL'], data=json.dumps(source_attributes), headers={"Content-Type": ""})
        r.get_method = lambda: 'PUT'
        r = urllib2.urlopen(r)
        LOG.debug("response: %s", r.read())
    except:
        LOG.exception("Exception in reply_to_msg")
        return False

    return True

def generate_skey(region, keyname):
    mypath = os.path.dirname(os.path.realpath(__file__))

    conn = boto.ec2.connect_to_region(region)
    keypair = conn.create_key_pair(keyname)
    keypair.save(mypath)

    return os.path.join(mypath, keyname+".pem")

def execute_playbook(keypath, pbvars):
    return_data = {}

    extra_vars = {}

    ignorerrors = pbvars.pop('IgnorePlaybookFailure', 'no')
    for k,v in pbvars.iteritems():
        if type(v) == dict and len(v) == 1:
            fname = v.keys()[0]
            if fname == "VMSeriesHelper::ConvertToEC2DNS":
                args = v[fname]
                if len(args) != 2:
                    return False, "VMSeriesHelper::ConvertToEC2DNS requires 2 args"
                ip, region = args
                toks = ip.split('.')
                v = "ec2-%s-%s-%s-%s.%s.compute.amazonaws.com"%(toks[0], toks[1], toks[2], toks[3], region)
                LOG.debug("Converted IP: %s", v)

                return_data[k] = v
        extra_vars[k] = v

    extra_vars['key_filename'] = keypath

    mypath = os.path.dirname(os.path.realpath(__file__))
    module_path = os.path.join(mypath, 'ansible-pan', 'library')
    playbook = os.path.join(mypath, "vm-series-playbook.yml")

    playbook_cb = HelperPlaybookCallbacks()
    runner_cb = HelperRunnerCallbacks()
    inventory = ansible.inventory.Inventory(host_list="localhost,127.0.0.1")
    stats = ansible.callbacks.AggregateStats()

    pb = ansible.playbook.PlayBook(
            playbook=playbook,
            module_path=module_path,
            callbacks=playbook_cb,
            runner_callbacks=runner_cb,
            extra_vars=extra_vars,
            stats=stats,
            inventory=inventory
        )
    pb.run()

    if ignorerrors == 'yes':
        return True, "okey dokey", return_data

    if len(pb.stats.dark) != 0:
        return False, "Ansible: Unreachable", None
    if len(pb.stats.failures) != 0:
        return False, "Ansible: Playbook failed", None

    return True, "okey dokey", return_data

def main(args):
    global EVENTS

    awsregion = os.environ['AWS_REGION']
    sqsurl = os.environ['AWS_SQS_URL']

    stackname = os.environ['STACKNAME']
    keypath = generate_skey(awsregion, stackname)

    sqsconn = boto.sqs.connect_to_region(awsregion)
    queue = boto.sqs.queue.Queue(connection=sqsconn, url=sqsurl)
    if queue is None:
        LOG.critical("No queue found")
        sys.exit(1)

    mypath = os.path.dirname(os.path.realpath(__file__))
    tpl = os.path.join(mypath, 'www', 'index.j2')
    target = os.path.join(mypath, 'www', 'index.html')
    EVENTS = WebEvents(tpl, target)

    EVENTS.add_waiting()
    while True:
        msg = queue.read(30)
        if msg == None:
            LOG.debug('no message')
            time.sleep(10)
            continue

        msgbody = json.loads(msg.get_body())
        LOG.debug("message: %s", msgbody['Message'])
        crmsg = json.loads(msgbody['Message'])

        rt = crmsg.get('RequestType', None)
        if rt == 'Create':
            try:
                success, reason, data = execute_playbook(keypath, crmsg.get('ResourceProperties', {}))
                LOG.debug("playbook result: %s %s %s", success, reason, data)
            except:
                LOG.exception("exception in execute_playbook")
                reply_to_msg(crmsg, success=False, reason="Exception executing playbook")
            else:
                reply_to_msg(crmsg, success=success, reason=reason, data=data)
            EVENTS.add_waiting()
        elif rt == 'Delete':
            reply_to_msg(crmsg, success=True, reason="OK")
        else:
            LOG.warning("Unhandled RequestType %s", rt)
            reply_to_msg(crmsg, succces=True, reason="OK")

        queue.delete_message(msg)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main(sys.argv[:1])


