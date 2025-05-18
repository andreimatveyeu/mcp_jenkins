import subprocess
import os

def main():
    script_path = os.path.join(os.path.dirname(__file__), 'docker', 'run.client')
    subprocess.run(['/bin/sh', script_path])

if __name__ == '__main__':
    main()
