__all__ = ('template',)

template = """

[cloudstack]
apiurl = http://mgmt1-dtc1.avira-cloud.net:8080/client/api
apikey =
secretkey =
domainid = 29
zoneid = 6
templateid = 519
serviceid = 17
cloudinit_puppet = http://joe.avira-cloud.net/autodeploy/vdt-puppet-agent.cloudinit
cloudinit_base = http://joe.avira-cloud.net/autodeploy/vdt-base.cloudinit
"""
