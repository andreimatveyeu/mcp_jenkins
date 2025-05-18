import pytest
import requests
import time
import subprocess
import os

# Configuration
SERVER_PORT = os.getenv("SERVER_PORT", "8000")
SERVER_URL = f"http://localhost:{SERVER_PORT}"
MCP_API_KEY_FOR_TESTS = os.getenv("MCP_API_KEY")
# The server application is main.py, located at /app/src/jenkins_mcp_server/main.py inside the container
# (as per Dockerfile WORKDIR /app and COPY commands)
SERVER_COMMAND = ["python", "/app/src/jenkins_mcp_server/main.py"]
STARTUP_TIMEOUT = 15  # seconds to wait for server to start (increased slightly for Flask startup)

@pytest.fixture(scope="module")
def server_process():
    print(f"Starting server with command: {' '.join(SERVER_COMMAND)}")
    process = subprocess.Popen(SERVER_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait for the server to start
    start_time = time.time()
    server_ready = False
    while time.time() - start_time < STARTUP_TIMEOUT:
        try:
            response = requests.get(f"{SERVER_URL}/health", timeout=1) # Assuming a /health endpoint
            if response.status_code == 200:
                print("Server started successfully.")
                server_ready = True
                break
        except requests.ConnectionError:
            time.sleep(0.5) # Wait and retry
        except requests.Timeout:
            print("Server health check timed out, retrying...")
            time.sleep(0.5)

    if not server_ready:
        stdout, stderr = process.communicate()
        print(f"Server failed to start within {STARTUP_TIMEOUT} seconds.")
        print(f"STDOUT: {stdout.decode()}")
        print(f"STDERR: {stderr.decode()}")
        process.terminate()
        process.wait()
        pytest.fail(f"Server did not start on {SERVER_URL} within {STARTUP_TIMEOUT}s")
        return None # Should not reach here due to pytest.fail

    yield process
    
    print("Terminating server process...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("Server process did not terminate gracefully, killing.")
        process.kill()
    print("Server process terminated.")

def test_server_is_running(server_process):
    """Test that the server starts and is accessible."""
    assert server_process is not None, "Server process fixture failed to run."
    try:
        # Example: Test a basic endpoint, assuming server has a root endpoint or a health check
        response = requests.get(SERVER_URL + "/") 
        # Or, if you have a specific health check endpoint:
        # response = requests.get(SERVER_URL + "/health")
        assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
        print(f"Successfully connected to server at {SERVER_URL}/")
    except requests.ConnectionError as e:
        pytest.fail(f"Failed to connect to the server at {SERVER_URL}. Error: {e}")
    except requests.Timeout:
        pytest.fail(f"Request to the server at {SERVER_URL} timed out.")

def test_list_jobs_recursive(server_process):
    """Test recursive listing of jobs."""
    assert server_process is not None, "Server process fixture failed to run."
    assert MCP_API_KEY_FOR_TESTS, "MCP_API_KEY environment variable must be set for these tests"
    
    headers = {"X-API-Key": MCP_API_KEY_FOR_TESTS}

    # Non-recursive call
    try:
        response_non_recursive = requests.get(f"{SERVER_URL}/jobs", headers=headers, timeout=10)
        response_non_recursive.raise_for_status() # Raise an exception for HTTP error codes
    except requests.RequestException as e:
        pytest.fail(f"Failed to get non-recursive job list: {e}")
    
    assert response_non_recursive.status_code == 200, \
        f"Expected 200 OK for non-recursive /jobs, got {response_non_recursive.status_code}. Response: {response_non_recursive.text}"
    
    jobs_data_non_recursive = response_non_recursive.json().get("jobs")
    assert isinstance(jobs_data_non_recursive, list), "Expected 'jobs' to be a list in non-recursive response"
    
    actual_jobs_non_recursive = [j for j in jobs_data_non_recursive if j.get("type") != "folder" and "_class" in j]
    count_actual_jobs_non_recursive = len(actual_jobs_non_recursive)
    print(f"Non-recursive actual jobs found ({count_actual_jobs_non_recursive}):")
    for job in actual_jobs_non_recursive:
        print(f"  - {job.get('name')}")

    # Recursive call
    try:
        response_recursive = requests.get(f"{SERVER_URL}/jobs?recursive=true", headers=headers, timeout=20) # Longer timeout for potentially slow recursive calls
        response_recursive.raise_for_status()
    except requests.RequestException as e:
        pytest.fail(f"Failed to get recursive job list: {e}")

    assert response_recursive.status_code == 200, \
        f"Expected 200 OK for recursive /jobs, got {response_recursive.status_code}. Response: {response_recursive.text}"

    jobs_data_recursive = response_recursive.json().get("jobs")
    assert isinstance(jobs_data_recursive, list), "Expected 'jobs' to be a list in recursive response"

    actual_jobs_recursive = [j for j in jobs_data_recursive if j.get("type") != "folder" and "_class" in j]
    count_actual_jobs_recursive = len(actual_jobs_recursive)
    print(f"Recursive actual jobs found ({count_actual_jobs_recursive}):")
    for job in actual_jobs_recursive:
        print(f"  - {job.get('name')}")

    assert count_actual_jobs_recursive >= count_actual_jobs_non_recursive, \
        (f"Recursive actual job count ({count_actual_jobs_recursive}) "
         f"should be >= non-recursive actual job count ({count_actual_jobs_non_recursive})")

    if count_actual_jobs_recursive > count_actual_jobs_non_recursive:
        # If recursion found more actual jobs, these additional jobs must come from subfolders.
        non_recursive_job_names = {job['name'] for job in actual_jobs_non_recursive}
        recursive_job_names = {job['name'] for job in actual_jobs_recursive}
        
        newly_found_job_names = recursive_job_names - non_recursive_job_names
        
        assert newly_found_job_names, \
            ("Expected to find new job names in recursive call when its count is higher, "
             "but the set difference is empty. This indicates a logic issue or identical job names despite different counts.")

        found_nested_in_newly_found = False
        for name in newly_found_job_names:
            if "/" in name:
                found_nested_in_newly_found = True
                print(f"Found newly listed nested job: {name}")
                break
        
        assert found_nested_in_newly_found, \
            (f"When recursive call ({count_actual_jobs_recursive} jobs) finds more actual jobs than non-recursive "
             f"({count_actual_jobs_non_recursive} jobs), at least one of the *newly found* jobs "
             f"must be a nested job (name containing '/'). Newly found jobs: {newly_found_job_names}. "
             "If this fails, recursive listing might not be correctly fetching from subfolders or test data is insufficient.")
             
    elif count_actual_jobs_recursive > 0 and count_actual_jobs_recursive == count_actual_jobs_non_recursive:
        # If counts are equal, check if any listed jobs are nested (could indicate non-recursive is also finding them, or all jobs are top-level)
        found_any_nested_job = False
        for job in actual_jobs_recursive: # Could use either list as counts are same
            if "/" in job.get("name", ""):
                found_any_nested_job = True
                break
        assert found_any_nested_job, \
            (f"Recursive and non-recursive calls found the same number of actual jobs ({count_actual_jobs_recursive}), "
             f"but no jobs with '/' in their names (indicative of subfolders) were identified. "
             f"Given that subfolder jobs are expected in this environment, this indicates they were not found by the recursive call "
             f"or not named with the conventional '/' separator. "
             f"Jobs found: {[j.get('name') for j in actual_jobs_recursive]}")

    print("Recursive job listing test completed.")
