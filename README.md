# mcp_jenkins

A Jenkins MCP server. Model Context Protocol (MCP) lets AI tools (like chatbots) talk to and control your Jenkins setup, i. e. retrieve information and modify settings. 

**Note:** This is a minimal experimental version of the MCP Jenkins server and is currently in early development.

## Description

This project provides a Model Context Protocol (MCP) server for interacting with Jenkins. It allows users to trigger Jenkins jobs, get build statuses, and perform other Jenkins-related operations through the MCP interface.

## Components

*   [`server.py`](src/mcp_jenkins/server.py): The core MCP Jenkins server application.
*   [`functions_schema.md`](src/mcp_jenkins/functions_schema.md): Defines the schema for the functions exposed by the MCP Jenkins server.
*   [`client.py`](src/mcp_jenkins/client.py): An example client demonstrating how to interact with the MCP Jenkins server (provided for reference only).
*   [`functional tests`](src/mcp_jenkins/tests/functional/test_server.py): Contains functional tests for the MCP Jenkins server.

## Installation

To install the package, run:

```bash
pip install .
```

## Usage

### Common Workflows

#### Running the Server

To run the MCP server:

```bash
./docker/run.server
```

#### Running the Example Client

To run the example client:

```bash
./docker/run.client
```

For example, to list builds for a job named "backups" using a specific model, you can run:

```bash
./docker/run.client --model gemini-2.0-flash-001 "list builds backups"
```

Note: If the package is installed via `pip install .`, the `mcp_jenkins_client` console script is also available.

This might produce output similar to:

```
Query: list builds backups
Result:
Recent builds for backups:
  - Build #1086: FAILURE (http://myjenkins:8080/job/backups/1086/)
```

#### Building and Testing with Docker

A common workflow for development and testing is to first build the Docker image and then execute the tests:

1.  **Build the Docker image:**
    This step prepares the environment needed for testing.
    ```bash
    ./docker/build
    ```

2.  **Run tests:**
    After the build is complete, execute the tests.
    ```bash
    ./docker/run.tests
    ```
This sequence ensures that tests are performed against the latest build in a consistent Dockerized environment.

#### Deploying a Test Environment

To deploy a local Jenkins testing instance (without authentication, for functional tests only):

```bash
./docker/deploy.test.environment
```

### Connecting to an Existing Jenkins Instance

To use the MCP Jenkins server with an existing Jenkins instance, you need to configure the following environment variables:

*   `JENKINS_URL`: The full URL of your Jenkins instance (e.g., `http://your-jenkins-host:8080`). This is **required**.
*   `JENKINS_USER`: (Optional) Your Jenkins username if authentication is required.
*   `JENKINS_API_TOKEN`: (Optional) Your Jenkins API token. This must be provided along with `JENKINS_USER` if authentication is used. You can generate an API token in your Jenkins user's configuration page (`<Jenkins URL>/me/configure`).
*   `MCP_API_KEY`: A secret API key to secure this MCP server. Requests to the MCP server will need to include this key in the `X-API-Key` header. This is **required** unless `DEBUG_MODE` is set to `true`.
*   `DEBUG_MODE`: Set to `true` to run the MCP server in debug mode, which bypasses the `MCP_API_KEY` requirement and provides more verbose logging. **Do not use in production.**

**Example Configuration (Bash):**

```bash
export JENKINS_URL="http://your-jenkins-host:8080"
export JENKINS_USER="your_jenkins_username"
export JENKINS_API_TOKEN="your_jenkins_api_token"
export MCP_API_KEY="your_mcp_secret_key"
# export DEBUG_MODE="true" # Uncomment for development/testing without MCP_API_KEY
```

Once these environment variables are set, you can run the MCP server using the Docker script:

```bash
./docker/run.server
```

The MCP server will then attempt to connect to your specified Jenkins instance.

## OpenWebUI Integration

The file `open-webui/open_webui_interface.py` provides an example of how to integrate this MCP Jenkins server with an OpenWebUI instance.

To use it:
1. In your OpenWebUI interface, navigate to the section for adding or configuring tools.
2. Create a new tool.
3. Copy the entire content of the `open-webui/open_webui_interface.py` file and paste it into the tool configuration in OpenWebUI.
4. **Important**: You will need to adjust the connection parameters within the pasted code, specifically:
    - `MCP_JENKINS_SERVER_URL`: Set this environment variable in your OpenWebUI environment to the URL of your running MCP Jenkins server (e.g., `http://localhost:5000`). The script defaults to `http://localhost:5000` if the variable is not set.
    - `MCP_API_KEY`: If your MCP Jenkins server is configured to require an API key, ensure this environment variable is set in your OpenWebUI environment. The script will print a warning if it's not found but will still attempt to make requests.

Once configured, the tools defined in `open_webui_interface.py` (e.g., `list_jobs`, `trigger_build`, `get_build_status`) should become available for use within your OpenWebUI chat interface.

## License

This project is licensed under the MIT License.
