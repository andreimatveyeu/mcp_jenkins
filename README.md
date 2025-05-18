# mcp_jenkins

A Jenkins MCP server. Model Context Protocol (MCP) lets AI tools (like chatbots) talk to and control your Jenkins setup, i. e. retrieve information and modify settings. 

**Note:** This is a minimal experimental version of the MCP Jenkins server and is currently in early development.

## Description

This project provides a Model Context Protocol (MCP) server for interacting with Jenkins. It allows users to trigger Jenkins jobs, get build statuses, and perform other Jenkins-related operations through the MCP interface.

## Installation

To install the package and make the console scripts available, run:

```bash
pip install .
```

## Usage

Once the package is installed using `pip install .`, the following console scripts become available in your shell environment:

*   `mcp_jenkins_server`: Runs the MCP server.
*   `mcp_jenkins_client`: Runs an example client.
*   `mcp_jenkins_run_docker_build`: Builds the Docker image for the server. This should be run before executing tests.
*   `mcp_jenkins_run_docker_tests`: Runs tests using Docker (e.g., server/client/server tests). This script typically requires the Docker image to be built first using `mcp_jenkins_run_docker_build`.

These scripts eliminate the need to manually manage Python paths or install requirements separately if the package has been installed.

### Common Workflows

#### Running the Server

To run the MCP server using the installed script:

```bash
mcp_jenkins_server
```

#### Running the Example Client

To run the example client using the installed script:

```bash
mcp_jenkins_client
```

For example, to list builds for a job named "backups" using a specific model, you can run:

```bash
mcp_jenkins_client --model gemini-2.0-flash-001 "list builds backups"
```

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
    mcp_jenkins_run_docker_build
    ```

2.  **Run tests:**
    After the build is complete, execute the tests.
    ```bash
    mcp_jenkins_run_docker_tests
    ```
This sequence ensures that tests are performed against the latest build in a consistent Dockerized environment.

## License

This project is licensed under the MIT License.
