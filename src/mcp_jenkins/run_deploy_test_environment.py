import subprocess
import sys
import os
import time
import shutil

def run_command(command, check=True, capture_output=False, text=True, shell=False):
    """Runs a shell command and handles potential errors."""
    print(f"Executing command: {' '.join(command) if isinstance(command, list) else command}")
    try:
        result = subprocess.run(
            command,
            check=check,
            capture_output=capture_output,
            text=text,
            shell=shell
        )
        if capture_output and result.stdout:
            print("Stdout:")
            print(result.stdout.strip())
        if capture_output and result.stderr:
            print("Stderr:")
            print(result.stderr.strip())
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e}", file=sys.stderr)
        if e.stdout:
            print(f"Stdout:\n{e.stdout.strip()}", file=sys.stderr)
        if e.stderr:
            print(f"Stderr:\n{e.stderr.strip()}", file=sys.stderr)
        sys.exit(e.returncode)
    except FileNotFoundError:
        print(f"Error: Command not found. Make sure the necessary tools (docker, curl) are installed and in your PATH.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    output_file = "test_envs"
    jenkins_image = "jenkins/jenkins:2.504.1-lts-alpine"
    jenkins_container_name = "mcp-jenkins-test-env"
    jenkins_port = "6211"
    jenkins_user = "testadmin"
    jenkins_password = "test"
    jenkins_url = f"http://localhost:{jenkins_port}"

    # Check if test environment already exists
    if os.path.exists(output_file) and os.path.isdir("test_jenkins_data") and any(os.scandir("test_jenkins_data")):
        print("--------------------------------------------------")
        print("Jenkins test environment already exists.")
        print(f"Credentials in '{output_file}':")
        with open(output_file, 'r') as f:
            print(f.read())
        print("")
        print(f"To reinstall the test environment, remove the '{output_file}' file and the 'test_jenkins_data' directory.")
        print("--------------------------------------------------")
        sys.exit(0)

    # Attempt to stop and remove the container if it exists
    print(f"Attempting to stop and remove existing container '{jenkins_container_name}' if it exists...")
    run_command(["docker", "rm", "-f", jenkins_container_name], check=False, capture_output=True)
    print("Ensured no conflicting container is running.")

    print("Starting Jenkins test environment deployment...")
    print("Pulling Jenkins image (if not already present)...")
    run_command(["docker", "pull", jenkins_image])

    print(f"Starting Jenkins container '{jenkins_container_name}' on port {jenkins_port}...")
    # Create the secrets directory if it doesn't exist
    os.makedirs("test_jenkins_data/secrets", exist_ok=True)
    run_command([
        "docker", "run", "-d", "--network=main", "--name", jenkins_container_name,
        "-p", f"{jenkins_port}:8080",
        "-v", f"{os.path.join(os.getcwd(), 'test_jenkins_data')}:/var/jenkins_home",
        "-e", "JAVA_OPTS=-Djenkins.install.runSetupWizard=true",
        jenkins_image
    ])

    print(f"Waiting for Jenkins to start at {jenkins_url} (this may take a few minutes)...")
    max_retries = 90
    retry_count = 0
    while retry_count < max_retries:
        try:
            # Use curl with silent, fail, write-out http_code, and output to null
            result = run_command(
                ["curl", "-sL", "--fail", "-w", "%{http_code}", f"{jenkins_url}/login", "-o", "/dev/null"],
                check=False,
                capture_output=True
            )
            if result.returncode == 0 and "200" in result.stdout:
                print("Jenkins is up and running!")
                break
        except Exception:
            pass # Ignore exceptions during curl check

        retry_count += 1
        if retry_count >= max_retries:
            print(f"Error: Jenkins did not start within the expected time ({max_retries * 5} seconds).", file=sys.stderr)
            print(f"Attempting to get logs from container '{jenkins_container_name}':", file=sys.stderr)
            run_command(["docker", "logs", jenkins_container_name], check=False)
            sys.exit(1)

        print(f"Jenkins not ready yet (attempt {retry_count}/{max_retries}). Retrying in 5 seconds...")
        time.sleep(5)

    print("Allowing Jenkins an additional 20 seconds to initialize fully...")
    time.sleep(20)

    print("Retrieving initial admin password...")
    initial_admin_password = ""
    max_password_retries = 60 # Wait up to 5 minutes (60 * 5 seconds)
    password_retry_count = 0
    while not initial_admin_password and password_retry_count < max_password_retries:
        password_retry_count += 1
        print(f"Attempting to retrieve initial admin password (attempt {password_retry_count}/{max_password_retries})...")
        try:
            # Use docker exec to cat the password file
            result = run_command(
                ["docker", "exec", jenkins_container_name, "cat", "/var/jenkins_home/secrets/initialAdminPassword"],
                check=False,
                capture_output=True
            )
            if result.returncode == 0 and len(result.stdout.strip()) == 32:
                initial_admin_password = result.stdout.strip()
                print("Initial admin password retrieved successfully.")
                break
            else:
                 print(f"Password not found or incorrect length ({len(result.stdout.strip())}). Retrying in 5 seconds...")
        except Exception:
            pass # Ignore exceptions during password retrieval

        time.sleep(5)

    if not initial_admin_password:
        print(f"Error: Failed to retrieve initial admin password after {max_password_retries} attempts.", file=sys.stderr)
        run_command(["docker", "logs", jenkins_container_name], check=False)
        sys.exit(1)

    print(f"Initial admin password: {initial_admin_password}")
    print("")

    print(f"Creating initial '{output_file}' with basic credentials...")
    os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
    with open(output_file, 'w') as f:
        f.write("# Jenkins Test Environment Credentials\n")
        f.write("# Generated by script\n")
        f.write(f"export JENKINS_URL=\"{jenkins_url}\"\n")
        f.write(f"export JENKINS_USER=\"{jenkins_user}\"\n")
        f.write(f"export JENKINS_PASSWORD=\"{jenkins_password}\"\n")
        f.write("export JENKINS_API_TOKEN=\"your-api-token-created-manually\"\n")

    print("--------------------------------------------------")
    print("Jenkins deployment and initial credential file creation complete!")
    print(f"Initial credentials saved in: {os.path.realpath(output_file)}")
    print("")
    print(f"Please manually finalize the Jenkins setup via the UI ({jenkins_url}).")
    print(f"1. Unlock Jenkins using the initial admin password provided above.")
    print("2. Install suggested plugins or select plugins.")
    print(f"3. Create the user '{jenkins_user}' with the password '{jenkins_password}'.")
    print(f"4. Manually generate an API token for the user '{jenkins_user}' via the Jenkins UI.")
    print(f"5. Manually add the API token to the '{output_file}' as:")
    print("   export JENKINS_API_TOKEN=\"your_api_token_here\"")
    print("")
    print(f"To stop the Jenkins container: docker stop {jenkins_container_name}")
    print(f"To remove the Jenkins container (after stopping): docker rm {jenkins_container_name}")
    print(f"To view Jenkins logs: docker logs {jenkins_container_name}")
    print("--------------------------------------------------")

if __name__ == '__main__':
    main()
