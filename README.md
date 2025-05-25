# mcp_jenkins

A Jenkins MCP server. Model Context Protocol (MCP) lets AI tools (like chatbots) talk to and control your Jenkins setup, i. e. retrieve information and modify settings. 

**Note:** This is a minimal experimental version of the MCP Jenkins server and is currently in early development.

## Description

This project provides a Model Context Protocol (MCP) server for interacting with Jenkins. It allows users to trigger Jenkins jobs, get build statuses, and perform other Jenkins-related operations through the MCP interface.

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
