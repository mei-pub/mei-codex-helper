#!/usr/bin/env python3
"""MeiCodexHelper 证书生成 (core 层, 纯 Python 无 openssl 依赖)

生成 mkcert 式 CA + 叶子证书链(SAN localhost+127.0.0.1), 输出到 AS/certs。
用法: python certs.py ensure | gen
  ensure - 存在即跳过; gen - 强制重生成
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

CA_CN = 'MeiCodexHelper Local CA'


def _ensure_cryptography():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    return x509, hashes, serialization, rsa, NameOID


def gen(force=False):
    ca_pem = os.path.join(paths.CERTS_DIR, 'ca.pem')
    if not force and all(os.path.exists(os.path.join(paths.CERTS_DIR, f))
                         for f in ('ca.pem', 'cert.pem', 'key.pem')):
        return '证书已存在, 跳过'
    x509, hashes, serialization, rsa, NameOID = _ensure_cryptography()
    os.makedirs(paths.CERTS_DIR, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)

    # CA
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_CN)])
    ca_cert = (x509.CertificateBuilder()
               .subject_name(ca_name).issuer_name(ca_name)
               .public_key(ca_key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1))
               .not_valid_after(now + datetime.timedelta(days=3650))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=False, key_cert_sign=True,
                                            crl_sign=True, content_commitment=False,
                                            key_encipherment=False, data_encipherment=False,
                                            key_agreement=False, encipher_only=False,
                                            decipher_only=False), critical=True)
               .sign(ca_key, hashes.SHA256()))

    # Leaf (SAN: localhost + 127.0.0.1)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    leaf_cert = (x509.CertificateBuilder()
                 .subject_name(leaf_name).issuer_name(ca_name)
                 .public_key(leaf_key.public_key())
                 .serial_number(x509.random_serial_number())
                 .not_valid_before(now - datetime.timedelta(days=1))
                 .not_valid_after(now + datetime.timedelta(days=3650))
                 .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                 .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False,
                                              crl_sign=False, content_commitment=False,
                                              key_encipherment=True, data_encipherment=False,
                                              key_agreement=False, encipher_only=False,
                                              decipher_only=False), critical=True)
                 .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                                critical=False)
                 .add_extension(x509.SubjectAlternativeName([
                     x509.DNSName('localhost'), x509.IPAddress(ipaddress_module().IPv4Address('127.0.0.1'))]),
                     critical=False)
                 .sign(ca_key, hashes.SHA256()))

    def w(path, data):
        with open(path, 'wb') as f:
            f.write(data)

    w(ca_pem, ca_cert.public_bytes(serialization.Encoding.PEM))
    w(os.path.join(paths.CERTS_DIR, 'ca.key'),
      ca_key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()))
    w(os.path.join(paths.CERTS_DIR, 'key.pem'),
      leaf_key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))
    leaf_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
    ca_pem_bytes = ca_cert.public_bytes(serialization.Encoding.PEM)
    w(os.path.join(paths.CERTS_DIR, 'cert.pem'), leaf_pem + ca_pem_bytes)  # fullchain
    return '已生成新证书链'


def ipaddress_module():
    import ipaddress
    return ipaddress


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'ensure'
    print(gen(force=(cmd == 'gen')))
