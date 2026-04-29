from netmiko import ConnectHandler
import datetime
import os

def scrape_switch():
    device = {
        'device_type': 'brocade_fastiron',
        'host': '172.16.1.15',
        'username': 'admin',
        'use_keys': True,
        'key_file': os.path.expanduser('~/.ssh/id_rsa_brocade'),
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
