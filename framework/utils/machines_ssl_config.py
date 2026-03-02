import os

KNOWN_MACHINE_CERTS = {"ro": "/etc/ssl/ca-bundle.pem"}

def get_machine(verbose=0):
    try:
        hostname = os.uname()[1]  # Index 1 of the tuple is the hostname
        if verbose>0: print("The hostname is:", hostname)
    except AttributeError:
        print("os.uname() is not available on this platform.")
        hostname = None
    return hostname

def conform_machine_ssl_certs(verbose=0):
    hostname = get_machine(verbose=verbose)
    try:
        host = hostname.split('-')[0].strip()
    except Exception as hst_err:
        print("[!] This machine {hostname} is not conformable")
        return
    assert host in KNOWN_MACHINE_CERTS.keys(), "[!] {hostname} is not conformable"
    try:
        cert_file = os.environ['SSL_CERT_FILE']
        if verbose>0: print(f"[+][CHECK PASS] SSL_CERT_FILE found: {cert_file}")
    except Exception as file_err:
        print(f"[+][CHECK FAIL] SSL_CERT_FILE NOT found")
        cert_file = None
    if cert_file is None:
        cert_file = KNOWN_MACHINE_CERTS[host]
        os.environ['SSL_CERT_FILE'] = cert_file
        print(f"[+] {hostname} SSL_CERT_FILE set OK: {cert_file}")
    else:
        if (cert_file != KNOWN_MACHINE_CERTS[host]):
            print(f"[!] {hostname} is using the wrong SSL_CERT_FILE: {cert_file}")
            cert_file = KNOWN_MACHINE_CERTS[host]
            os.environ['SSL_CERT_FILE'] = cert_file
            print(f"[+] {hostname} SSL_CERT_FILE corrected OK: {cert_file}")
        else:
            if verbose>0: print(f"[+] {hostname} is using the correct SSL_CERT_FILE: {cert_file}")
