import sys
import os

UPSTART = """description "VM-Series-Helper"
author "techbizdev@paloaltonetworks.com"

start on runlevel [2345]
stop on runlevel [!2345]

env AWS_REGION=%(awsregion)s
env AWS_ACCESS_KEY_ID=%(awsaccesskey)s
env AWS_SECRET_ACCESS_KEY=%(awssecretaccesskey)s
env "KEYNAME=%(keyname)s"
env "AWS_SQS_URL=%(awssqsurl)s"

respawn

exec python %(helper)s >> /var/log/helper.log 2>&1
"""

def main(args):

    environment = {
        'awsregion': os.environ['AWS_REGION'],
        'awsaccesskey': os.environ['AWS_ACCESS_KEY_ID'],
        'awssecretaccesskey': os.environ['AWS_SECRET_ACCESS_KEY'],
        'keyname': os.environ['KEYNAME'],
        'awssqsurl': os.environ['AWS_SQS_URL'],
        'helper': os.path.join(os.path.dirname(os.path.realpath(__file__)), 'helper.py')
    }

    print 'installing upstart script'
    f = open('/etc/init/vmsh.conf', 'w+b')
    f.write(UPSTART%environment)
    f.close()

if __name__ == "__main__":
    main(sys.argv[1:])
