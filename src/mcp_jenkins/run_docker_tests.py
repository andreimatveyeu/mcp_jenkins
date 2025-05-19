import subprocess
import sys
import os

os.environ["MCP_API_KEY"] = "2"

def main():
    # Construct the path to the script relative to this file's location
    # This script is in src/mcp_jenkins/
    # The target script is in src/mcp_jenkins/docker/run.server.tests
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "docker", "run.server.tests")

    try:
        # Ensure the script is executable
        if not os.access(script_path, os.X_OK):
            print(f"Info: Script {script_path} is not executable. Attempting to make it executable (chmod +x).")
            os.chmod(script_path, 0o755) # rwxr-xr-x

        # Execute the script
        # Pass arguments from this wrapper script to the target script
        process = subprocess.run([script_path] + sys.argv[1:], check=True, text=True, capture_output=True)
        if process.stdout:
            print(process.stdout.strip())
        if process.stderr:
            print(process.stderr.strip(), file=sys.stderr)
        sys.exit(process.returncode)
    except subprocess.CalledProcessError as e:
        print(f"Error executing script {script_path}: {e}", file=sys.stderr)
        if e.stdout:
            print(f"Stdout:\n{e.stdout.strip()}", file=sys.stderr)
        if e.stderr:
            print(f"Stderr:\n{e.stderr.strip()}", file=sys.stderr)
        sys.exit(e.returncode)
    except FileNotFoundError:
        print(f"Error: Script {script_path} not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred while trying to run {script_path}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
