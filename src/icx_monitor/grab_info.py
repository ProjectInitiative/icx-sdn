from netmiko import ConnectHandler
import datetime
import os

def _req_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"Error: {key} must be set", file=__import__("sys").stderr)
        __import__("sys").exit(1)
    return val

def scrape_switch():
    host = _req_env("ICX_SWITCH_HOST")
    username = _req_env("ICX_SWITCH_USER")
    password = os.environ.get("ICX_SSH_PASSWORD")
    key_file = os.environ.get("ICX_SSH_KEY")

    if not password and not key_file:
        print("Error: set ICX_SSH_KEY or ICX_SSH_PASSWORD", file=__import__("sys").stderr)
        __import__("sys").exit(1)

    device = {
        'device_type': 'brocade_fastiron',
        'host': host,
        'username': username,
        'use_keys': not password,
        'key_file': key_file,
        'password': password,
        'disabled_algorithms': {"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]},
        'allow_agent': False,
    }

    try:
        with ConnectHandler(**device) as net_connect:
            if not net_connect.check_enable_mode():
                net_connect.enable()

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"switch_data_{ts}.log"

            commands = [
                "show running-config",
                "show interface brief",
                "show lag",
                "show chassis",
                "show statistics brief"
            ]

            print(f"Authenticated! Streaming data to {filename}...")
            with open(filename, "w") as f:
                for cmd in commands:
                    print(f"  > {cmd}")
                    f.write(f"\n--- START {cmd} ---\n")
                    f.write(net_connect.send_command(cmd, delay_factor=2))
                    f.write(f"\n--- END {cmd} ---\n")

            print(f"Success!")

    except Exception as e:
        print(f"Connection failed: {e}")

def main():
    scrape_switch()

if __name__ == "__main__":
    main()
