import subprocess
import os
import sys

def main():
    script_path = os.path.join(os.path.dirname(__file__), 'docker', 'run.client')
    # Pass command-line arguments received by this script to the shell script
    command = ['/bin/sh', script_path] + sys.argv[1:]
    subprocess.run(command)

if __name__ == '__main__':
    main()
