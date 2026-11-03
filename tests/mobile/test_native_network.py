"""Native Rust HTTPS/WSS against an isolated, schema-shaped synthetic peer."""
import datetime
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID

ROOT=Path(__file__).resolve().parents[2]


def certificate(tmp_path,*,host="127.0.0.1",server_auth=True):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic mobile test CA')])
    now=datetime.datetime.now(datetime.timezone.utc)
    ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True).add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False).sign(key,hashes.SHA256())
    leaf_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])).issuer_name(name).public_key(leaf_key.public_key()).serial_number(2).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]),False).add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server_auth else ExtendedKeyUsageOID.CLIENT_AUTH]),False).add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),False).sign(key,hashes.SHA256())
    (tmp_path/'key.pem').write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (tmp_path/'cert.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    return ca.public_bytes(serialization.Encoding.PEM).decode()


@pytest.mark.parametrize('mode',['duel','untrusted','redirect','name-mismatch','wrong-usage'])
def test_native_transport_and_recovery(tmp_path,mode):
    binary=os.environ.get('PTCG_MOBILE_NETWORK_PROBE')
    if not binary:pytest.skip('Build native network_probe and set PTCG_MOBILE_NETWORK_PROBE')
    # A real TLS server certificate needs serverAuth under Apple's policy.
    ca=certificate(tmp_path,host='127.0.0.2' if mode=='name-mismatch' else '127.0.0.1',server_auth=mode!='wrong-usage');report=tmp_path/'report.json'
    config=tmp_path/'peer.json';config.write_text(json.dumps({'key':str(tmp_path/'key.pem'),'cert':str(tmp_path/'cert.pem'),'report':str(report),'redirect':mode=='redirect'}))
    server=subprocess.Popen(['node',str(ROOT/'tests/mobile/tls-peer.mjs'),str(config),str(ROOT/'tests/synthetic/protocol/v2.json')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        import selectors
        poll=selectors.DefaultSelector();poll.register(server.stdout,selectors.EVENT_READ)
        assert poll.select(15),'Synthetic peer did not start';origin=json.loads(server.stdout.readline())['origin'];poll.close()
        native=tmp_path/'native.json';native.write_text(json.dumps({'origin':origin,'caPem':'' if mode=='untrusted' else ca}))
        result=subprocess.run([binary,str(native),mode],cwd=tmp_path,capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
        value=json.loads(result.stdout);assert value['status']=='passed'
        if mode=='duel':assert value['wss'] and value['memberReplaySeparation'] and value['unknownCommandRecoveredAfterReopen']
    finally:
        server.terminate()
        try:server.communicate(timeout=5)
        except subprocess.TimeoutExpired:server.kill();server.communicate()
    counts=json.loads(report.read_text());assert counts['originHeaders']==counts['cookieHeaders']==counts['redirectFollowed']==0
    if mode=='duel':assert counts['sessions']==2 and counts['commands']==2 and counts['upgrades']==1
    else:assert counts['sessions']==0
