import subprocess

import boto.ec2
import boto.vpc

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
    #make the promt colored
    prompt = "\033[92mec2>\033[0m "

    def __init__(self):
        region = None

        for r in boto.ec2.regions(aws_access_key_id=cfg.ACCESSKEY,
                                  aws_secret_access_key=cfg.SECRETKEY,
                                  debug=2):
            if r.name == cfg.REGION:
                region = r
                break

        self.client = boto.ec2.connect_to_region(cfg.REGION,
                                                 aws_access_key_id=cfg.ACCESSKEY,
                                                 aws_secret_access_key=cfg.SECRETKEY,
                                                 debug=2)

        self.vpc = boto.vpc.VPCConnection(region=region,
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
                    print "%(displayname)20s %(id)20s %(state)15s   %(dns)s" % {'displayname' : i.tags['Name'] if 'Name' in i.tags else 'N/A',
                                      'id' : i.id,
                                      'state' : i._state,
                                      'dns': i.dns_name}
        #pretty.machine_print(instances)

    def do_create_keypair(self, keypair_name, path=None):
        """
        Create a new keypair.
        If it should be saved to disk, specify a folder, where the key is saved as a file with the name <keypair_name>.
        e.g. "ec2> create_keypair mykey /home/user/keys/" will save the keyfile in "/home/user/keys/mykey".

        Usage::

            ec2> create_keypair name [/path/to/folder/]
        """
        keypair = self.client.create_key_pair(keypair_name)
        print "%25s - %s"%(keypair.name, keypair.fingerprint)
        if path:
            try:
                keypair.save(path)
            except Exception, e:
                print "couldn't save key: %s"%e


    def do_delete_keypair(self, keypair_name):
        """
        Delete a keypair

        Usage::

            ec2> delete_keypair name
        """
        print self.client.delete_key_pair(keypair_name)

    def do_deploy(self, ami, key_name, displayname, security_groups, base=False, **userdata):
        """
        Create a vm with a specific name and add some userdata.

        Usage::

            ec2> deploy <ami> <key_name> <name> <security-groups> <userdata>
                    optional: <base>

        To specify the puppet role in the userdata, which will install and
        configure the machine according to the specified role use::

            ec2> deploy ami-c1aaabb5 ssh_key loadbalancer1 default role=lvs

        To specify additional user data, specify additional keywords::

            ec2> deploy loadbalancer1 role=lvs environment=test etc=more

        This will install the machine as a Linux virtual server.

        If you don't want pierrot-agent (puppet agent) automatically installed,
        you can specify 'base' as a optional parameter. This is needed for the
        puppetmaster which needs manual installation::

            ec2> deploy puppetmaster base role=puppetmaster

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
        ud = UserData(cloudinit_url, cfg.PUPPETMASTER, **userdata).formatted_data()
        response = self.client.run_instances(ami,
                                             key_name=key_name,
                                             instance_type=cfg.INSTANCE_TYPE,
                                             security_groups=security_groups.split(","),
                                             user_data=ud)

        # Set instance name
        instance = response.instances[0]
        self.client.create_tags([instance.id], {"Name": displayname})

        # we add the machine id to the cert req file, so the puppet daemon
        # can sign the certificate
        if not base:
            add_pending_certificate(instance.id)

        print "%s started, machine id %s" % (displayname, instance.id)

    def do_deploy_vpc(self, displayname, ami, key_name, security_groups, subnet_id, base=False, **userdata):
        """
        Create a vm in a VPC, with a specific name and add some userdata.

        Usage::

            ec2> deploy_vpc <name> <ami> <key_name> <security-groups-ids> <subnet_id> <userdata>
                    optional: <base>

        To specify the puppet role in the userdata, which will install and
        configure the machine according to the specified role use::

            ec2> deploy_vpc loadbalancer1 ami-c1aaabb5 ssh_key default role=lvs

        To specify additional user data, specify additional keywords::

            ec2> deploy_vpc loadbalancer1 role=lvs environment=test etc=more

        This will install the machine as a Linux virtual server.

        If you don't want pierrot-agent (puppet agent) automatically installed,
        you can specify 'base' as a optional parameter. This is needed for the
        puppetmaster which needs manual installation::

            ec2> deploy_vpc puppetmaster base role=puppetmaster

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

        try:
            cloudinit_url = cfg.CLOUDINIT_BASE if base else cfg.CLOUDINIT_PUPPET
            ud = UserData(cloudinit_url, cfg.PUPPETMASTER, **userdata).formatted_data()
            response = self.client.run_instances(ami,
                key_name=key_name,
                instance_type=cfg.INSTANCE_TYPE,
                subnet_id=subnet_id,
                security_group_ids=security_groups.split(","),
                user_data=ud)

            # Set instance name
            instance = response.instances[0]
            self.client.create_tags([instance.id], {"Name": displayname})

            # we add the machine id to the cert req file, so the puppet daemon
            # can sign the certificate
            if not base:
                add_pending_certificate(instance.id)

            print "%s started, machine id %s" % (displayname, instance.id)
        except Exception as e:
            print "EXCEPTION: ", e

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
        print "rebooting instance id {0}".format(instance_id)
        self.client.reboot_instances(instance_ids=[instance_id])

    def do_list(self, resource_type):
        """
        List information about current EC2 configuration.

        Usage::

            ec2> list <regions|key-pairs|eip|images|placement-groups|volumes|security-groups
                 vpc-subnets|vpc-customer-gateways|vpc-internet-gateways|vpc-vpn-gateways|
                 vpc-vpn-connections|vpcs>
        """

        if resource_type == "regions":
            for r in self.client.get_all_regions():
                print r.name
        elif resource_type == "key-pairs":
            print "{0:<15}\t{1:<15}\t{2}".format("Name", "Region", "Fingerprint")
            for i in self.client.get_all_key_pairs():
                print "{0:<15}\t{1:<15}\t{2}".format(i.name, i.region.name, i.fingerprint)
        elif resource_type == "eip":
            print "%17s\t%15s\t%s" % ("address", "region", "instance")
            for r in self.client.get_all_addresses():
                print "%17s\t%15s\t%s" % (r.public_ip, r.region.name, r.instance_id)
        elif resource_type == "placement-groups":
            print "{0:<15}\t{1:<15}\t{2:<15}\t{3:<15}".format("Name", "Region", "Strategy", "State")
            for r in self.client.get_all_placement_groups():
                print "{0:<15}\t{1:<15}\t{2:<15}\t{3:<15}".format(r.name, r.region.name, r.strategy, r.state)
        elif resource_type == "volumes":
            print "{0:<15}\t{1:<15}\t{2:<20}\t{3:<10}\t{4:<15}\t{5:<15}\t{6}".format("Id", "Region", "Snapshot", "Size", "Status", "Zone", "Created")
            for r in self.client.get_all_volumes():
                print "{0:<15}\t{1:<15}\t{2:<20}\t{3:<10}\t{4:<15}\t{5:<15}\t{6}".format(r.id,
                                                                                         r.region.name,
                                                                                         r.snapshot_id,
                                                                                         r.size,
                                                                                         r.status,
                                                                                         r.zone,
                                                                                         r.create_time)
        elif resource_type == "security-groups":
            print "{0:<15}\t{1:<15}\t{2:<15}\t{3:<20}\t{4:<20}\t{5:<20}".format("Id", "Region", "VPC", "Name", "Ingress", "Egress")
            for r in self.client.get_all_security_groups():
                printed_first_line = False
                for ingress_rule, egress_rule in map(None, r.rules, r.rules_egress):
                    if not printed_first_line:
                        printed_first_line = True
                        print "{0:<15}\t{1:<15}\t{2:<15}\t{3:<20}\t{4:<20}\t{5:<20}".format(r.id,
                                                                               r.region.name,
                                                                               r.vpc_id,
                                                                               r.name,
                                                                               ingress_rule and ingress_rule or "",
                                                                               egress_rule and egress_rule or "")
                    else:
                        print "{0:<15}\t{1:<15}\t{2:<15}\t{3:<20}\t{4:<20}\t{5:<20}".format("",
                                                                                            "",
                                                                                            "",
                                                                                            "",
                                                                                            ingress_rule and ingress_rule or "",
                                                                                            egress_rule and egress_rule or "")
        elif resource_type == "vpc-subnets":
            for subnet in self.vpc.get_all_subnets():
                pprint.pprint(vars(subnet))
                print subnet
        elif resource_type == "vpc-customer-gateways":
            for cgw in self.vpc.get_all_customer_gateways():
                pprint.pprint(vars(cgw))
                print cgw
        elif resource_type == "vpc-internet-gateways":
            for igw in self.vpc.get_all_internet_gateways():
                pprint.pprint(vars(igw))
                print igw
        elif resource_type == "vpc-vpn-gateways":
            for vgw in self.vpc.get_all_vpn_gateways():
                pprint.pprint(vars(vgw))
                print vgw
        elif resource_type == "vpc-vpn-connections":
            for c in self.vpc.get_all_vpn_connections():
                pprint.pprint(vars(c))
                print c
        elif resource_type == "vpcs":
            for v in self.vpc.get_all_vpcs():
                pprint.pprint(vars(v))
                print v
        else:
            print "Not implemented"

    def do_vpc(self, request_type):
        """
        VPC related operations

        Usage::

           ec2> vpc <subnet>
        """
        pass

    def do_request(self, request_type):
        """
        Request a public elastic ip address

        Usage::

            ec2> request eip
        """
        if request_type == "eip":
            response = self.client.allocate_address()
            print "created eip address {0}".format(response.public_ip)
        else:
            print "Not implemented"

    def do_release(self, request_type, public_ip):
        """
        Release a public ip address with a specific id.

        Usage::

            ec2> release eip <public_ip>
        """
        if request_type == "eip":
            print "releasing ip address {0}".format(public_ip)
            self.client.release_address(public_ip=public_ip)
        else:
            print "Not implemented"


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
