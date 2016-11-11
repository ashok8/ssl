from lib.system.satest import *
from lib.sa.sa import Sa
from lib.sa.sacluster import SaCluster
from lib.templates.network_template import NetworkTemplate
from lib.templates.cluster_template import ClusterTemplate
from lib.templates.mysql_failover_template import MysqlFailoverTemplate
from lib.system.satest import collections, satest_obj, TestCaseException
from lib.sql.mysqlserver import MySqlServer
from lib.sql.mysqlclient import MySqlClient
import os

# from helper import *

cluster_name = "ssl_cluster"
server_roles = ["Read + Write", "Read + Write"]
db_name = "mysql"
ssl_cipher = "AES256-SHA"

def get_test_spec():
    spec = collections.OrderedDict()

    spec["mysql_client1_node"] = {"type": "node", "format": "linux"}
    spec["mysql_server1_node"] = {"type": "node", "format": "linux"}

    spec["scalearc1_node"] = {"type": "node", "format": "scalearc"}
    spec["cluster_type"] = {"type": "string", "format": "enum",
                            "enum_list": ["ALL",
                                          SaCluster.MYSQL_TYPE]}
    spec["test_cases"] = {"type": "string", "format": "enum",
                          "enum_list": ["ALL",
                                        "No AutoFailover if AutoFailover flag is disabled",
                                        "No AutoFailover if no Standby Servers",
                                        "AutoFailover with synchronous replication"],
                          "default": "ALL"}
    return spec


def run_tests(scalearc_property, test_property, tc):
    client_class = test_property.client_class
    cluster_type = test_property.cluster_type
    client_mgmt_ip = test_property.client_mgmt_ip
    client_mgmt_username = test_property.client_mgmt_username
    client_mgmt_password = test_property.client_mgmt_password

    # Should be based on the final test-bed setup
    vip_ip = scalearc_property['vip_list'][0]
    sa_inbound_ip = vip_ip
    sa_outbound_ip = vip_ip
    cluster_inbound_port = test_property.cluster_inbound_port

    cluster_id = None  # We will determin cluster id later

    sa_obj = Sa(host_ip=scalearc_property['mgmt_ip'],
                username=scalearc_property['mgmt_username'],
                password=scalearc_property['mgmt_password'],
                ui_username=scalearc_property['ui_username'],
                ui_password=scalearc_property['ui_password'])

    db_server_obj = MySqlServer(host_ip=test_property.dbs[0]['mgmt_ip'],
                                password=test_property.dbs[0]['mgmt_password'],
                                username=test_property.dbs[0]['mgmt_username'],
                                db_host=test_property.dbs[0]['ip'],
                                db_port=test_property.dbs[0]['port'],
                                db_username=test_property.dbs[0]['username'],
                                db_password=test_property.dbs[0]['password'])

    client_obj = client_class(host_ip=client_mgmt_ip,
                              username=client_mgmt_username,
                              password=client_mgmt_password,
                              db_name=db_name,  # TODO make it a variable
                              db_username=test_property.dbs[0]['username'],
                              db_host=sa_inbound_ip,
                              db_port=cluster_inbound_port,
                              db_password=test_property.dbs[0]['password'],
                              service_name=None)

    id = 0
    if True:  # Basic Setup must always run
        purpose = """
        1. To create required SSL certs
        2. Upload certs in ScaleArc and DB server
        """
        steps = """
        1. Create a cluster with one DB server
        2. Create Required Certs
        3. Upload Server cert & keys and CA certs on  ScaleArc
        4. Upload Certs in DB server and make necessary changes in /etc/my.cnf
        5. Restart mysql server
        6. Stop Cluster
        7. Enable SSL offload
        8. Start Cluster
        """
        satest_obj.start_test_section(id=id, description="Basic Setup ", purpose=purpose,
                                      steps=steps)
        test_result = SaTest.FAILED
        try:

            sa_obj.initialize()

            checkpoint = "Adding VIP"
            network_template_obj = NetworkTemplate(sa_obj=sa_obj)

            satest_obj.test_assert(network_template_obj.deploy_vip(
                vip=sa_inbound_ip,
                interface_name=scalearc_property["mgmt_interface"],
                subnet=scalearc_property["mgmt_subnet"]),
                ex=TestScriptException(checkpoint))

            # Creating cluster
            cs_obj = sa_obj.cluster.ClusterCreate(cluster_type=cluster_type,
                                                  cluster_name=cluster_name,
                                                  cluster_inbound_ip=sa_inbound_ip,
                                                  cluster_outbound_ip=sa_outbound_ip,
                                                  cluster_server_username=test_property.dbs[0]["username"],
                                                  cluster_server_password=test_property.dbs[0]["password"],
                                                  cluster_lb_type="Dynamic",
                                                  cluster_inbound_port=str(cluster_inbound_port))
            cs_obj.append_cluster_servers(server_ip=test_property.dbs[0]["ip"],
                                          server_port=str(test_property.dbs[0]["port"]),
                                          server_role=server_roles[0])

            result = cs_obj.add()
            satest_obj.test_assert(result['success'], ex=TestScriptException("Create cluster"))

            cluster_id = sa_obj.cluster.get_cluster_id_by_name(cluster_name=cluster_name)
            result = sa_obj.cluster.start_cluster(cluster_id=cluster_id)
            satest_obj.test_assert(result, ex=TestScriptException("Ensure cluster started"))

            # Creating Required Certs
            temp_dir = '/tmp/' + satest_obj.get_unique() + '_mysql_certs'
            create_dir = 'mkdir -p ' + temp_dir + ';' + 'cd ' + temp_dir + ';'
            create_certs = create_dir + 'openssl req -new -newkey rsa:2048 -days 3600 -nodes -x509 -subj "/C=IN/ST=MH/L=Mumbai/O=SA/CN=www.scalearc.com" -keyout ca-key.pem -out ca-cert.pem; ' \
                                        'openssl req -new -newkey rsa:2048 -days 3600 -nodes -x509 -subj "/C=IN/ST=MH/L=Mumbai/O=SA/CN=www.scalearc.com" -keyout server-key.pem -out server-req.pem; ' \
                                        'openssl rsa -in server-key.pem -out server-key.pem; ' \
                                        'openssl x509 -req -in server-req.pem -days 3600 -CA ca-cert.pem -CAkey ca-key.pem -set_serial 01 -out server-cert.pem; ' \
                                        'openssl req -new -newkey rsa:2048 -days 3600 -nodes -x509 -subj "/C=IN/ST=MH/L=Mumbai/O=SA/CN=www.scalearc.com" -keyout client-key.pem -out client-req.pem; ' \
                                        'openssl rsa -in client-key.pem -out client-key.pem; openssl x509 -req -in client-req.pem -days 3600 -CA ca-cert.pem -CAkey ca-key.pem -set_serial 01 -out client-cert.pem;'

            os.system(create_certs)

            file_path_ca = db_server_obj.get_temp_file_path("ca-cert.pem")
            file_path_server_cert = db_server_obj.get_temp_file_path("server-req.pem")
            file_path_server_key = db_server_obj.get_temp_file_path("server-key.pem")

            # Copying Certs to DB server
            satest_obj.scp(target_host_obj=db_server_obj, source_file_path=temp_dir + '/ca-cert.pem',
                           target_file_path=file_path_ca)
            satest_obj.scp(target_host_obj=db_server_obj, source_file_path=temp_dir + '/server-req.pem',
                           target_file_path=file_path_server_cert)
            satest_obj.scp(target_host_obj=db_server_obj, source_file_path=temp_dir + '/server-key.pem',
                           target_file_path=file_path_server_key)

            # Taking backup of /etc/my.cnf
            db_server_obj.copy(destination_filename="/tmp/my.cnf_ssl_bk", source_filename="/etc/my.cnf")

            # Making necessary changes in /etc/my.cnf
            '''
            replace_ssl_path = 'sed -i \'s/^ssl-ca=/#ssl-ca=/\' /etc/my.cnf;' \
                        'sed -i \'s/^ssl-cert=/#ssl-cert=/\' /etc/my.cnf;' \
                        'sed -i \'s/^ssl-key=/#ssl-key=/\' /etc/my.cnf;' \
                        'sed -i \'s/^ssl-cipher=/#ssl-cipher=/\' /etc/my.cnf;' \
                        'echo "ssl-ca=/tmp/ca-cert.pem" >> /etc/my.cnf;' \
                        'echo "ssl-cert=/tmp/server-req.pem" >> /etc/my.cnf;' \
                        'echo "ssl-key=/tmp/server-key.pem" >> /etc/my.cnf;' \
                        'echo "ssl-cipher=DHE-RSA-AES256-SHA:AES256-SHA" >> /etc/my.cnf;'
            db_server_obj.service(service_name="mysqld")
            db_server_obj.cmd(replace_ssl_path)
            '''

            # Uploading SSL certs on ScaleArc
            sa_obj.cluster.upload_ssl_certs_keys(cluster_id=sa_obj.cluster.get_cluster_id_by_name("ssl_cluster"),
                                                 server_cert=temp_dir + '/server-req.pem',
                                                 server_key=temp_dir + '/server-key.pem',
                                                 server_ca=temp_dir + '/ca-cert.pem')


            checkpoint = "Check whether cluster stopped successfully "
            api_result = sa_obj.cluster.ClusterIdStop(cluster_id=cluster_id).update()
            satest_obj.test_assert(result=api_result["success"], ex=TestCaseException(checkpoint))

            checkpoint = "Check whether SSL Offload gets enabled"
            api_result = sa_obj.cluster.ClusterIdSslEnabled(cluster_id=cluster_id, ssl_enabled='on').update()
            satest_obj.test_assert(result=api_result["success"], ex=TestCaseException(checkpoint))

            sa_obj.cluster.start_cluster(cluster_id=cluster_id)
            test_result = SaTest.PASSED
        except Exception as ex:
            satest_obj.critical(ex.message)
        satest_obj.set_result(result=test_result, cluster_type=cluster_type)


    id = 2537
    tc_name = "Establish SSL connection through an SSL cluster"
    if (tc == "ALL") or (tc == tc_name):
        purpose = """
        To Check if ScaleArc can process established SSL connection through an SSL cluster.
        """
        steps = """
        1. Open SSL Connection through ScaleArc cluster
        2. Ensure whether Connection is encrypted and provided cipher is being used
        3. Check for fired query in query log
                """
        try:
            satest_obj.start_test_section(id=id,
                                          description=tc_name,
                                          steps=steps,
                                          purpose=purpose)
            test_result = SaTest.FAILED

            cluster_id = sa_obj.cluster.get_cluster_id_by_name(cluster_name=cluster_name)
            checkpoint = "clear query logs"
            clear_logs = sa_obj.cluster.clear_currentlogs_idb_log(cluster_id=cluster_id)
            satest_obj.test_assert(clear_logs, ex=TestCaseException(checkpoint))

            query = "select 123"
            traffic_result = client_obj.generic_command(server_ip=sa_inbound_ip,
                                                        server_port=cluster_inbound_port,
                                                        db_name=db_name,
                                                        username=test_property.dbs[0]['username'],
                                                        password=test_property.dbs[0]['password'],
                                                        cmd_list=[query],
                                                        ssl_cipher=ssl_cipher)
            satest_obj.test_assert(traffic_result['success'], ex=TestCaseException("Traffic sent successfully"))
            satest_obj.sleep("Getting log trail", 10)

            log_result = sa_obj.cluster.get_logtrail(cluster_id=cluster_id)
            log_result.reverse()

            ssl_found = False
            ssl_index = -1

            for ssl_index in range(len(log_result)):
                if log_result[ssl_index].ssl:
                    ssl_found = True
                    break

            satest_obj.test_assert(ssl_found, ex=TestCaseException("Index of SSL log found: %d" % (ssl_index)))
            satest_obj.test_assert_expected(expected="Yes", actual=log_result[ssl_index].ssl,
                                            ex=TestCaseException("SSL Connection established"))
            satest_obj.test_assert_expected(expected=ssl_cipher, actual=log_result[ssl_index].ssl_cipher,
                                            ex=TestCaseException("SSL Cipher matched"))

            query_found = False
            for index in range(ssl_index, len(log_result)):
                if query in log_result[index].query:
                    query_found = True
                    break
            satest_obj.test_assert(query_found, ex=TestCaseException("fired Query: %s found" % (query)))
            test_result = SaTest.PASSED
        except Exception as ex:
            satest_obj.critical(ex.message)
        finally:
            db_server_obj.copy(destination_filename="/etc/my.cnf", source_filename="/tmp/my.cnf_ssl_bk")
        satest_obj.set_result(result=test_result,
                              cluster_type=cluster_type,
                              rerun_parameters={"cluster_type": cluster_type})


if __name__ == "__main__":
    test_variants = []
    satest_obj.initialize()
    cluster_type = satest_obj.get_test_input("cluster_type")
    scalearc = satest_obj.get_node_properties(satest_obj.get_test_input("scalearc1_node"))
    vip_ip = scalearc["vip_list"][0]

    if cluster_type == SaCluster.MYSQL_TYPE:
        mysql_client1_node = satest_obj.get_node_properties(satest_obj.get_test_input("mysql_client1_node"))
        mysql_server1_node = satest_obj.get_node_properties(satest_obj.get_test_input("mysql_server1_node"))
        mysql_variant = DbTestVariant(MySqlServer.name)
        mysql_variant.cluster_type = SaCluster.MYSQL_TYPE
        mysql_variant.client_class = MySqlClient
        mysql_variant.server_class = MySqlServer
        mysql_variant.vip_ip = scalearc["vip_list"][0]

        mysql_variant.dbs = []
        db = {}
        db['mgmt_ip'] = mysql_server1_node['mgmt_ip']
        db['mgmt_username'] = mysql_server1_node['mgmt_username']
        db['mgmt_password'] = mysql_server1_node['mgmt_password']
        db['username'] = mysql_server1_node['db_username']
        db['password'] = mysql_server1_node['db_password']
        db['ip'] = mysql_server1_node['mgmt_ip']
        db['port'] = mysql_server1_node['db_port']
        mysql_variant.dbs.append(db)

        mysql_variant.client_mgmt_ip = mysql_client1_node['mgmt_ip']
        mysql_variant.client_mgmt_username = mysql_client1_node['mgmt_username']
        mysql_variant.client_mgmt_password = mysql_client1_node['mgmt_password']

        mysql_variant.cluster_inbound_port = 3306

    tc = satest_obj.get_test_input("test_cases")

    if cluster_type == "ALL":
        test_variants.append(mysql_variant)
    elif cluster_type == SaCluster.MYSQL_TYPE:
        test_variants.append(mysql_variant)
    try:
        for test_variant in test_variants:
            run_tests(scalearc_property=scalearc, test_property=test_variant, tc=tc)
    except Exception as ex:
        satest_obj.critical(ex.message)
    satest_obj.close()
