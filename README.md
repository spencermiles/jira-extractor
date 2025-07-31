# JIRA Issue Extractor

A command-line tool to extract JIRA issues and their changelogs using JQL queries. Can output to JSON (default) or SQLite database for analysis.

## Features

- Extract JIRA issues using JQL queries
- Fetch issue changelogs with full history
- **JSON output by default** - more useful for most analysis tasks
- Optional SQLite database output for structured storage
- **Parent issue and linked issues support** - captures relationships between issues
- **Epic and sprint information** - extracts epic keys/names and sprint details
- Story points extraction from customfield_10026
- Support for JIRA Cloud API v3
- Configurable result limits
- **Concurrent changelog fetching** for improved performance
- **Connection pooling** and automatic retry logic
- Comprehensive error handling and logging

## Installation

### Option 1: Using uv (Recommended)

The easiest way to use this tool is with [uv](https://docs.astral.sh/uv/), which automatically manages dependencies:

1. Install uv:
```bash
# On macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via pip
pip install uv
```

2. Make the script executable (optional):
```bash
chmod +x jira_dump.py
```

3. Set up your JIRA credentials:
```bash
cp .env.example .env
# Edit .env with your JIRA URL, username, and API token
```

That's it! No need to manage virtual environments or install dependencies manually.

### Option 2: Traditional Python Setup

1. Install Python dependencies:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

2. Set up your JIRA credentials:
```bash
cp .env.example .env
# Edit .env with your JIRA URL, username, and API token
```

## Usage

### Using uv (Recommended)

With uv, dependencies are automatically installed the first time you run the script:

```bash
# Run directly (if script is executable)
./jira_dump.py --jql "project = OS2 AND resolved >= \"2025-04-01\" ORDER BY created DESC"

# Or use uv run explicitly
uv run jira_dump.py --jql "project = PROJ AND status = 'In Progress'" --output issues.json

# Output to JSON file
uv run jira_dump.py --jql "project = PROJ" --output issues.json

# Compact JSON (no pretty-printing)
uv run jira_dump.py --jql "project = PROJ" --output issues.json --no-pretty

# Extract without changelogs for faster processing
uv run jira_dump.py --jql "project = PROJ" --no-changelogs --output issues.json
```

### SQLite Output

```bash
# Output to SQLite database
uv run jira_dump.py --jql "project = PROJ" --format sqlite --database my_data.db

# Use default database name (jira_data.db)
uv run jira_dump.py --jql "project = PROJ" --format sqlite
```

### Advanced Options

```bash
# Use more concurrent workers for faster changelog fetching
uv run jira_dump.py --jql "project = PROJ" --max-workers 20 --output issues.json

# Fetch more issues
uv run jira_dump.py --jql "project = PROJ" --max-results 2000 --output issues.json

# Using command line credentials (not recommended, use .env instead)
uv run jira_dump.py --jira-url https://your-company.atlassian.net \
                    --username your-email@company.com \
                    --api-token your-api-token \
                    --jql "project = PROJ"
```

### Traditional Python Usage

If you prefer to use traditional Python setup instead of uv:

```bash
# Activate your virtual environment first
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Then run with python
python jira_dump.py --jql "project = PROJ" --output issues.json
```

## Output Formats

### JSON Output (Default)

The JSON output includes comprehensive issue data with parsed descriptions and structured changelogs:

```json
[
  {
    "id": "10001",
    "key": "PROJ-123",
    "summary": "Fix login bug",
    "description": "Users cannot log in after recent update...",
    "issue_type": "Bug",
    "status": "In Progress", 
    "priority": "High",
    "assignee": "John Doe",
    "reporter": "Jane Smith",
    "created": "2024-01-15T10:30:00.000+0000",
    "updated": "2024-01-16T14:20:00.000+0000",
    "resolved": null,
    "project_key": "PROJ",
    "labels": ["security", "urgent"],
    "components": ["Authentication"],
    "fix_versions": ["v2.1.0"],
    "story_points": 5,
    "parent_key": "PROJ-100",
    "linked_issues": [
      {
        "type": "Blocks",
        "direction": "outward",
        "issue_key": "PROJ-124",
        "summary": "Update documentation"
      }
    ],
    "epic_key": "PROJ-50",
    "epic_name": "User Authentication Epic",
    "sprint_info": [
      {
        "id": 123,
        "name": "Sprint 15",
        "state": "ACTIVE",
        "start_date": "2024-01-15T09:00:00.000Z",
        "end_date": "2024-01-29T17:00:00.000Z",
        "complete_date": null
      }
    ],
    "changelogs": [
      {
        "change_id": "1001",
        "author": "John Doe",
        "created": "2024-01-16T09:15:00.000+0000",
        "field_name": "status",
        "field_type": "jira",
        "from_value": "1",
        "to_value": "3", 
        "from_string": "To Do",
        "to_string": "In Progress"
      }
    ]
  }
]
```

### SQLite Output

When using `--format sqlite`, data is stored in two tables:

#### Issues Table
- `id`: JIRA issue ID
- `key`: Issue key (e.g., PROJ-123)
- `summary`: Issue title
- `description`: Issue description (JSON if complex formatting)
- `issue_type`: Type (Bug, Story, etc.)
- `status`: Current status
- `priority`: Priority level
- `assignee`: Assigned user
- `reporter`: Reporter
- `created`: Creation date
- `updated`: Last update date
- `resolved`: Resolution date
- `project_key`: Project key
- `labels`: JSON array of labels
- `components`: JSON array of components
- `fix_versions`: JSON array of fix versions
- `story_points`: Story points from customfield_10026
- `parent_key`: Parent issue key (for subtasks/child issues)
- `linked_issues`: JSON array of linked issues with relationship info
- `epic_key`: Epic issue key (extracted from common custom fields)
- `epic_name`: Epic name/summary
- `sprint_info`: JSON array of sprint information with names, states, and dates
- `raw_data`: Complete JSON data from JIRA

#### Changelogs Table
- `issue_key`: Reference to issue
- `change_id`: Changelog entry ID
- `author`: User who made the change
- `created`: Change timestamp
- `field_name`: Changed field name
- `field_type`: Field type
- `from_value`: Previous value
- `to_value`: New value
- `from_string`: Previous display value
- `to_string`: New display value

## JQL Query Examples

- All issues in a project: `project = MYPROJECT`
- Issues created in the last 30 days: `created >= -30d`
- Issues assigned to you: `assignee = currentUser()`
- High priority bugs: `priority = High AND type = Bug`
- Recently updated issues: `updated >= '2024-01-01'`
- Parent issues only: `issueFunction in hasSubtasks()`
- Subtasks only: `issueFunction in subtasksOf("PROJ-100")`
- Issues in a specific epic: `"Epic Link" = "PROJ-50"`
- Issues in active sprints: `sprint in openSprints()`
- Issues in a specific sprint: `sprint = "Sprint 15"`

## Custom Field Detection

The tool automatically detects common custom fields for epics and sprints:

### Epic Fields
The tool tries these custom fields in order:
- `customfield_10014` (most common)
- `customfield_10008`
- `customfield_10002`

### Sprint Fields  
The tool tries these custom fields in order:
- `customfield_10020` (most common)
- `customfield_10010`
- `customfield_10004`

If your JIRA instance uses different custom field IDs, you can check the raw_data field in the output to identify the correct field names for your instance.

## Getting JIRA API Token

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Give it a label and copy the token
4. Use your email as username and the token as password

## Performance Options

The tool includes several performance optimizations:

- `--max-workers`: Number of concurrent workers for changelog fetching (default: 10)
- `--no-changelogs`: Skip changelog fetching for faster issue extraction
- Automatic connection pooling and retry logic
- Batch database operations (SQLite mode)

## Examples

Extract all bugs from the last week as JSON:
```bash
uv run jira_dump.py --jql "type = Bug AND created >= -7d" --output recent_bugs.json
```

Extract project issues to SQLite for analysis:
```bash
uv run jira_dump.py --jql "project = MYPROJECT" --format sqlite --database myproject.db
```

Quick extraction without changelogs:
```bash
uv run jira_dump.py --jql "project = PROJ AND assignee = currentUser()" --no-changelogs
```

Extract all issues from a specific epic:
```bash
uv run jira_dump.py --jql "\"Epic Link\" = \"PROJ-50\"" --output epic_issues.json
```

Extract issues from active sprints:
```bash
uv run jira_dump.py --jql "sprint in openSprints() AND project = PROJ" --output active_sprint.json
```

**Or run directly if the script is executable:**
```bash
./jira_dump.py --jql "type = Bug AND created >= -7d" --output recent_bugs.json
```