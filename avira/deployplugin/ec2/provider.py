import subprocess

import boto.ec2

from avira.deploy import api, pretty
from avira.deploy.clean import run_machine_cleanup, \
    remove_machine_port_forwards, node_clean, clean_foreman
from avira.deploy.userdata import UserData
from avira.deploy.utils import find_by_key, \
    find_machine, wrap, sort_by_key, is_puppetmaster, check_call_with_timeout
from avira.deploy.certificate import add_pending_certificate
from avira.deploy.config import cfg

__all__ = ('Provider',)

import pprint

class Provider(api.CmdApi):
    """ EC2 Deployment CMD Provider """
    prompt = "ec2> "

    def __init__(self):
        self.client = boto.ec2.connect_to_region(cfg.REGION,
                                                 aws_access_key_id=cfg.ACCESSKEY,
                                                 aws_secret_access_key=cfg.SECRETKEY,
                                                 debug=2)
        api.CmdApi.__init__(self)

    def do_status(self, mode=False):
        """
        Shows running instances, specify 'all' to show all instances

        Usage::

            ec2> status [detailed]
        """
        instances = []
        reservations = self.client.get_all_instances()
        for r in reservations:
            for i in r.instances:
                if mode == "detailed":
                    pprint.pprint(vars(i))
                else:
                    instances.append({'displayname' : i.tags['Name'] if 'Name' in i.tags else 'N/A',
                                      'id' : i.id,
                                      'state' : i._state,
                                      'dns': i.public_dns_name})
        pretty.machine_print(instances)

    def do_create_keypair(self, keypair_name):
        """
        Create a new keypair

        Usage::

            ec2> create_keypair name
        """
        print self.client.create_key_pair(keypair_name)

    def do_delete_keypair(self, keypair_name):
        """
        Delete a keypair

        Usage::

            ec2> delete_keypair name
        """
        self.client.delete_key_pair(keypair_name)

    def do_list_keypairs(self):
        """
        List existing keypairs

        Usage::

            ec2> list_keypairs
        """
        print "{0:<15}\t{1:<15}\t{2}".format("Name", "Region", "Fingerprint")
        for i in self.client.get_all_key_pairs():
            print dir(i)
            print "{0:<15}\t{1:<15}\t{2}".format(i.name, i.region.name, i.fingerprint)

    def do_deploy(self, image_id, key_name, displayname, base=False, networkids="", **userdata):
        """
        Create a vm with a specific name and add some userdata.

        Usage::

            ec2> deploy <image-id> <key_name> <displayname> <userdata>
                    optional: <base>

        To specify the puppet role in the userdata, which will install and
        configure the machine according to the specified role use::

            ec2> deploy loadbalancer1 role=lvs

        To specify additional user data, specify additional keywords::

            ec2> deploy loadbalancer1 role=lvs environment=test etc=more

        This will install the machine as a Linux virtual server.

        If you don't want pierrot-agent (puppet agent) automatically installed,
        you can specify 'base' as a optional parameter. This is needed for the
        puppetmaster which needs manual installation::

            ec2> deploy puppetmaster role=puppetmaster base

        """
        if not userdata:
            print "Specify the machine userdata, (at least it's role)"
            return

        #vms = self.client.listVirtualMachines({
        #    'domainid': cfg.DOMAINID
        #})

        #KILLED = ['Destroyed', 'Expunging']
        #existing_displaynames = \
        #    [x['displayname'] for x in vms if x['state'] not in KILLED]

        cloudinit_url = cfg.CLOUDINIT_BASE if base else cfg.CLOUDINIT_PUPPET
        ud = UserData(cloudinit_url, cfg.PUPPETMASTER, **userdata).base64()
        response = self.client.run_instances(image_id,
                                             key_name=key_name,
                                             instance_type=cfg.INSTANCE_TYPE,
                                             user_data=ud)

        #print dir(response)
        #print response
        #pprint.pprint(vars(response))
        #print "instance"
        #pprint.pprint(vars(response.instances[0]))

        instance = response.instances[0]

        self.client.create_tags([instance.id], {"Name": displayname})

        # we add the machine id to the cert req file, so the puppet daemon
        # can sign the certificate
        if not base:
            add_pending_certificate(instance.id)

        print "%s started, machine id %s" % (displayname, instance.id)


    def do_destroy(self, instance_id):
        """
        Destroy an instance.

        Usage::

            ec2> destroy <instance_id>
        """

        def get_machine_by_id(client, instance_id):
            reservations = client.get_all_instances()
            for r in reservations:
                for i in r.instances:
                    if i.id == instance_id:
                        return i
            return None

        #
        # List instances
        # determine which machine we're destroying
        #
        machine = get_machine_by_id(self.client, instance_id)

        if machine is None:
            print "No machine found with the id %s" % machine.id
        else:
            if is_puppetmaster(machine.id):
                print "You are not allowed to destroy the puppetmaster"
                return
            print "running cleanup job on %s." % (machine.tags['Name'] if 'Name' in machine.tags else 'N/A')
            run_machine_cleanup(machine)

            self.client.terminate_instances(instance_ids=[instance_id])

            # first we are also going to remove the portforwards
            # remove_machine_port_forwards(machine, self.client)

            # now we cleanup the puppet database and certificates
            print "running puppet node clean"
            node_clean(machine)

            # now clean all offline nodes from foreman
            clean_foreman()


    def do_start(self, instance_id):
        """
        Start a stopped machine.

        Usage::

            ec2> start <instance_id>
        """
        print "starting instance id {0}".format(instance_id)
        self.client.start_instances(instance_ids=[instance_id])


    def do_stop(self, instance_id):
        """
        Stop a running machine.

        Usage::

            ec2> stop <instance_id>
        """
        print "stopping instance id {0}".format(instance_id)
        self.client.stop_instances(instance_ids=[instance_id])

    def do_reboot(self, instance_id):
        """
        Reboot a running machine.

        Usage::

            ec2> reboot <instance_id>
        """
        self.client.reboot_instances(instance_ids=[instance_id])

    def do_list(self, resource_type):
        """
        List information about current EC2 configuration.

        Usage::

            cloudstack> list <templates|serviceofferings|regions|addresses|images
                          diskofferings|ip|networks|portforwardings|
                          firewall>
        """

        if resource_type == "templates":
            zone_map = {x['id']: x['name'] for x in self.client.listZones({})}
            templates = self.client.listTemplates({
                "templatefilter": "executable"
            })
            templates = sort_by_key(templates, 'name')
            pretty.templates_print(templates, zone_map)
        elif resource_type == "regions":
            for r in self.client.get_all_regions():
                print r.name
        elif resource_type == "addresses":
            for r in self.client.get_all_addresses():
                print r
        elif resource_type == "serviceofferings":
            serviceofferings = self.client.listServiceOfferings()
            pretty.serviceofferings_print(serviceofferings)

        elif resource_type == "diskofferings":
            diskofferings = self.client.listDiskOfferings()
            pretty.diskofferings_print(diskofferings)

        elif resource_type == "ip":
            ipaddresses = self.client.listPublicIpAddresses()
            pretty.public_ipaddresses_print(ipaddresses)

        elif resource_type == "networks":
            networks = self.client.listNetworks({
                'zoneid': cfg.ZONEID
            })
            networks = sort_by_key(networks, 'id')
            pretty.networks_print(networks)

        elif resource_type == "portforwardings":
            portforwardings = self.client.listPortForwardingRules({
                'domain': cfg.DOMAINID
            })
            portforwardings = sort_by_key(portforwardings, 'privateport')
            portforwardings.reverse()
            pretty.portforwardings_print(portforwardings)
        elif resource_type == "firewall":
            firewall_rules = self.client.listFirewallRules({
                'domain': cfg.DOMAINID
            })
            firewall_rules = sort_by_key(firewall_rules, 'ipaddress')
            firewall_rules.reverse()
            pretty.firewallrules_print(firewall_rules)
        else:
            print "Not implemented"

    def do_request(self, request_type):
        """
        Request a public ip address on the virtual router

        Usage::

            cloudstack> request ip
        """
        if request_type == "ip":
            response = self.client.associateIpAddress({
                'zoneid': cfg.ZONEID
            })
            print "created ip address with id %(id)s" % response

        else:
            print "Not implemented"

    def do_release(self, request_type, release_id):
        """
        Release a public ip address with a specific id.

        Usage::

            cloudstack> release ip <release_id>
        """
        if request_type == "ip":
            response = self.client.disassociateIpAddress({
                'id': release_id
            })
            print "releasing ip address, job id: %(jobid)s" % response
        else:
            print "Not implemented"

    def do_portfw(self, machine_id, ip_id, public_port, private_port):
        """
        Create a portforward for a specific machine and ip

        Usage::

            cloudstack> portfw <machine id> <ip id> <public port> <private port>

        You can get the machine id by using the following command::

            cloudstack> status

        You can get the listed ip's by using the following command::

            cloudstack> list ip
        """

        self.client.createPortForwardingRule({
            'ipaddressid': ip_id,
            'privateport': private_port,
            'publicport': public_port,
            'protocol': 'TCP',
            'virtualmachineid': machine_id
        })
        print "added portforward for machine %s (%s -> %s)" % (
            machine_id, public_port, private_port)

    def do_ssh(self, machine_id, ssh_public_port):
        """
        Make a machine accessible through ssh.

        Usage::

            cloudstack> ssh <machine_id> <ssh_public_port>

        This adds a port forward under the machine id to port 22 on the machine
        eg:

        machine id is 5034, after running::

            cloudstack> ssh 5034 22001

        I can now access the machine though ssh on all my registered ip
        addresses as follows::

            ssh ipaddress -p 22001
        """
        machines = self.client.listVirtualMachines({
            'domainid': cfg.DOMAINID
        })
        machine = find_machine(machine_id, machines)
        if machine is None:
            print "machine with id %s is not found" % machine_id
            return

        portforwards = wrap(self.client.listPortForwardingRules())

        def select_ssh_pfwds(pf):
            return pf.virtualmachineid == machine.id and pf.publicport == ssh_public_port
        existing_ssh_pfwds = filter(select_ssh_pfwds, portforwards)

        # add the port forward to each public ip, if it doesn't exist yet.
        ips = wrap(self.client.listPublicIpAddresses()['publicipaddress'])
        for ip in ips:
            current_fw = find_by_key(existing_ssh_pfwds, ipaddressid=ip.id)
            if current_fw is not None:
                print "machine %s already has a ssh portforward with ip %s to port %s" % (
                    machine_id, ip.ipaddress, ssh_public_port)
                continue
            else:
                self.client.createPortForwardingRule({
                    'ipaddressid': ip.id,
                    'privateport': "22",
                    'publicport': str(ssh_public_port),
                    'protocol': 'TCP',
                    'virtualmachineid': machine.id,
                    'openfirewall': "True",
                })
                print "machine %s is now reachable (via %s:%s)" % (
                    machine_id, ip.ipaddress, ssh_public_port)

    def do_kick(self, machine_id=None, role=None):
        """
        Trigger a puppet run on a server.

        This command only works when used on the puppetmaster.
        The command will either kick a single server or all server with a
        certian role.

        Usage::

            cloudstack> kick <machine_id>

        or::

            cloudstack> kick role=<role>

        """
        KICK_CMD = ['mco', "puppetd", "runonce", "-F"]
        if role is not None:
            KICK_CMD.append("role=%s" % role)
        else:
            machines = self.client.listVirtualMachines({
                'domainid': cfg.DOMAINID
            })
            machine = find_machine(machine_id, machines)
            if machine is None:
                print "machine with id %s is not found" % machine_id
                return
            KICK_CMD.append('hostname=%(name)s' % machine)

        try:
            print subprocess.check_output(KICK_CMD, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            print e.output

    def do_quit(self, _=None):
        """
        Quit the deployment tool.

        Usage::

            cloudstack> quit
        """
        return True

    def do_mco(self, *args, **kwargs):
        """
        Run mcollective

        Usage::

            cloudstack> mco find all
            cloudstack> mco puppetd status -F role=puppetmaster
        """
        command = ['mco'] + list(args) + ['%s=%s' % (key, value) for (key, value) in kwargs.iteritems()]
        check_call_with_timeout(command, 30)