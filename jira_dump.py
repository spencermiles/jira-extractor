#!/usr/bin/env python3
"""
JIRA Issue and Changelog Extractor

A command-line tool to extract JIRA issues and their changelogs using JQL queries.
Can output to JSON (default) or SQLite database. Automatically extracts story points,
parent issues, and linked issues.
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
import click
import requests
from requests.auth import HTTPBasicAuth
import os
from dotenv import load_dotenv
import concurrent.futures
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class JiraExtractor:
    """Main class for extracting JIRA issues and changelogs."""
    
    def __init__(self, jira_url: str, username: str, api_token: str, max_workers: int = 10):
        self.jira_url = jira_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, api_token)
        self.max_workers = max_workers
        self.session = self._create_session()
        
    def _create_session(self) -> requests.Session:
        """Create a session with connection pooling and retry strategy."""
        session = requests.Session()
        session.auth = self.auth
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        # Configure connection pooling
        adapter = HTTPAdapter(
            pool_connections=self.max_workers,
            pool_maxsize=self.max_workers * 2,
            max_retries=retry_strategy
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
        
    def get_issues(self, jql: str, max_results: int = 1000) -> List[Dict]:
        """Fetch issues using JQL query."""
        issues = []
        start_at = 0
        
        while True:
            url = f"{self.jira_url}/rest/api/3/search"
            
            # If max_results is 0 or -1, fetch all issues without limit
            if max_results <= 0:
                batch_size = 100
            else:
                batch_size = min(100, max_results - len(issues))
            
            params = {
                'jql': jql,
                'startAt': start_at,
                'maxResults': batch_size,
                'expand': 'changelog'
            }
            
            logger.info(f"Fetching issues {start_at} to {start_at + params['maxResults']}")
            
            try:
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                issues.extend(data['issues'])
                
                # Break if no more issues returned
                if len(data['issues']) < params['maxResults']:
                    break
                
                # Break if we've reached the specified limit (only when max_results > 0)
                if max_results > 0 and len(issues) >= max_results:
                    break
                    
                start_at += len(data['issues'])
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching issues: {e}")
                raise
                
        logger.info(f"Retrieved {len(issues)} issues")
        return issues
    
    def get_issue_changelog(self, issue_key: str) -> List[Dict]:
        """Fetch changelog for a specific issue."""
        url = f"{self.jira_url}/rest/api/3/issue/{issue_key}/changelog"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            return data['values']
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching changelog for {issue_key}: {e}")
            return []
    
    def get_changelogs_batch(self, issue_keys: List[str]) -> Dict[str, List[Dict]]:
        """Fetch changelogs for multiple issues concurrently."""
        changelogs = {}
        
        def fetch_changelog(issue_key: str) -> tuple:
            """Fetch changelog for a single issue and return tuple of (issue_key, changelog)."""
            try:
                changelog = self.get_issue_changelog(issue_key)
                return issue_key, changelog
            except Exception as e:
                logger.error(f"Error fetching changelog for {issue_key}: {e}")
                return issue_key, []
        
        # Use ThreadPoolExecutor for concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_key = {executor.submit(fetch_changelog, key): key for key in issue_keys}
            
            # Process completed tasks
            completed = 0
            for future in concurrent.futures.as_completed(future_to_key):
                issue_key, changelog = future.result()
                changelogs[issue_key] = changelog
                completed += 1
                
                if completed % 10 == 0 or completed == len(issue_keys):
                    logger.info(f"Fetched changelogs for {completed}/{len(issue_keys)} issues")
        
        return changelogs


class DatabaseManager:
    """Manages SQLite database operations."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
        self._lock = threading.Lock()
    
    def init_database(self):
        """Initialize the database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Issues table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    description TEXT,
                    issue_type TEXT,
                    status TEXT,
                    priority TEXT,
                    assignee TEXT,
                    reporter TEXT,
                    created TEXT,
                    updated TEXT,
                    resolved TEXT,
                    project_key TEXT,
                    labels TEXT,
                    components TEXT,
                    fix_versions TEXT,
                    story_points REAL,
                    parent_key TEXT,
                    linked_issues TEXT,
                    epic_key TEXT,
                    epic_name TEXT,
                    sprint_info TEXT,
                    api_url TEXT,
                    web_url TEXT,
                    raw_data TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Changelog table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS changelogs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_key TEXT NOT NULL,
                    change_id TEXT,
                    author TEXT,
                    created TEXT,
                    field_name TEXT,
                    field_type TEXT,
                    from_value TEXT,
                    to_value TEXT,
                    from_string TEXT,
                    to_string TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (issue_key) REFERENCES issues (key)
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def save_issues(self, issues: List[Dict]):
        """Save issues to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            for issue in issues:
                try:
                    # Extract issue data
                    fields = issue['fields']
                    
                    # Extract story points from customfield_10026
                    story_points = None
                    story_points_field = 'customfield_10026'
                    if story_points_field in fields:
                        story_points = fields[story_points_field]
                    
                    # Extract parent issue key (for subtasks)
                    parent_key = None
                    if 'parent' in fields and fields['parent']:
                        parent_key = fields['parent'].get('key')
                    
                    # Extract epic information (try common custom field IDs)
                    epic_key = None
                    epic_name = None
                    epic_fields = ['customfield_10014', 'customfield_10008', 'customfield_10002']
                    for epic_field in epic_fields:
                        if epic_field in fields and fields[epic_field]:
                            epic_data = fields[epic_field]
                            if isinstance(epic_data, dict):
                                epic_key = epic_data.get('key')
                                epic_name = epic_data.get('name') or epic_data.get('summary')
                            elif isinstance(epic_data, str):
                                epic_key = epic_data
                            break
                    
                    # Extract sprint information (try common custom field IDs)
                    sprint_info = []
                    sprint_fields = ['customfield_10020', 'customfield_10010', 'customfield_10004']
                    for sprint_field in sprint_fields:
                        if sprint_field in fields and fields[sprint_field]:
                            sprint_data = fields[sprint_field]
                            if isinstance(sprint_data, list):
                                for sprint in sprint_data:
                                    if isinstance(sprint, dict):
                                        sprint_info.append({
                                            'id': sprint.get('id'),
                                            'name': sprint.get('name'),
                                            'state': sprint.get('state'),
                                            'start_date': sprint.get('startDate'),
                                            'end_date': sprint.get('endDate'),
                                            'complete_date': sprint.get('completeDate')
                                        })
                                    elif isinstance(sprint, str):
                                        # Sometimes sprint data is a comma-separated string
                                        sprint_info.append({'name': sprint})
                            elif isinstance(sprint_data, dict):
                                sprint_info.append({
                                    'id': sprint_data.get('id'),
                                    'name': sprint_data.get('name'),
                                    'state': sprint_data.get('state'),
                                    'start_date': sprint_data.get('startDate'),
                                    'end_date': sprint_data.get('endDate'),
                                    'complete_date': sprint_data.get('completeDate')
                                })
                            break
                    
                    # Extract linked issues
                    linked_issues = []
                    if 'issuelinks' in fields and fields['issuelinks']:
                        for link in fields['issuelinks']:
                            link_data = {
                                'type': link.get('type', {}).get('name', ''),
                                'direction': None,
                                'issue_key': None,
                                'summary': None
                            }
                            
                            # Check if this issue is the inward or outward link
                            if 'inwardIssue' in link:
                                link_data['direction'] = 'inward'
                                link_data['issue_key'] = link['inwardIssue'].get('key')
                                link_data['summary'] = link['inwardIssue'].get('fields', {}).get('summary', '')
                            elif 'outwardIssue' in link:
                                link_data['direction'] = 'outward'  
                                link_data['issue_key'] = link['outwardIssue'].get('key')
                                link_data['summary'] = link['outwardIssue'].get('fields', {}).get('summary', '')
                            
                            if link_data['issue_key']:
                                linked_issues.append(link_data)
                    
                    # Extract URLs
                    api_url = issue.get('self', '')
                    # Construct web UI URL from the API URL or issue key
                    web_url = ''
                    if api_url:
                        # Extract base URL from API URL and construct browse URL
                        # API URL format: https://domain.atlassian.net/rest/api/3/issue/12345
                        # Web URL format: https://domain.atlassian.net/browse/ISSUE-KEY
                        base_url = api_url.split('/rest/api/')[0] if '/rest/api/' in api_url else ''
                        if base_url:
                            web_url = f"{base_url}/browse/{issue['key']}"
                    
                    issue_data = {
                        'id': issue['id'],
                        'key': issue['key'],
                        'summary': fields.get('summary', ''),
                        'description': json.dumps(fields.get('description')) if fields.get('description') else '',
                        'issue_type': fields.get('issuetype', {}).get('name', '') if fields.get('issuetype') else '',
                        'status': fields.get('status', {}).get('name', '') if fields.get('status') else '',
                        'priority': fields.get('priority', {}).get('name', '') if fields.get('priority') else '',
                        'assignee': fields.get('assignee', {}).get('displayName', '') if fields.get('assignee') else '',
                        'reporter': fields.get('reporter', {}).get('displayName', '') if fields.get('reporter') else '',
                        'created': fields.get('created', ''),
                        'updated': fields.get('updated', ''),
                        'resolved': fields.get('resolutiondate', ''),
                        'project_key': fields.get('project', {}).get('key', '') if fields.get('project') else '',
                        'labels': json.dumps(fields.get('labels', [])),
                        'components': json.dumps([c.get('name', '') for c in fields.get('components', [])]),
                        'fix_versions': json.dumps([v.get('name', '') for v in fields.get('fixVersions', [])]),
                        'story_points': story_points,
                        'parent_key': parent_key,
                        'linked_issues': json.dumps(linked_issues),
                        'epic_key': epic_key,
                        'epic_name': epic_name,
                        'sprint_info': json.dumps(sprint_info),
                        'api_url': api_url,
                        'web_url': web_url,
                        'raw_data': json.dumps(issue)
                    }
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO issues 
                        (id, key, summary, description, issue_type, status, priority, 
                         assignee, reporter, created, updated, resolved, project_key, 
                         labels, components, fix_versions, story_points, parent_key, 
                         linked_issues, epic_key, epic_name, sprint_info, api_url, web_url, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', tuple(issue_data.values()))
                    
                except Exception as e:
                    logger.error(f"Error saving issue {issue.get('key', 'unknown')}: {e}")
                    continue
            
            conn.commit()
            logger.info(f"Saved {len(issues)} issues to database")
    
    def save_changelogs_batch(self, changelogs_dict: Dict[str, List[Dict]]):
        """Save changelogs for multiple issues to the database in batch."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            total_changelog_items = 0
            for issue_key, changelogs in changelogs_dict.items():
                for changelog in changelogs:
                    try:
                        for item in changelog.get('items', []):
                            changelog_data = {
                                'issue_key': issue_key,
                                'change_id': changelog.get('id'),
                                'author': changelog.get('author', {}).get('displayName', '') if changelog.get('author') else '',
                                'created': changelog.get('created', ''),
                                'field_name': item.get('field', ''),
                                'field_type': item.get('fieldtype', ''),
                                'from_value': item.get('from', ''),
                                'to_value': item.get('to', ''),
                                'from_string': item.get('fromString', ''),
                                'to_string': item.get('toString', '')
                            }
                            
                            cursor.execute('''
                                INSERT INTO changelogs 
                                (issue_key, change_id, author, created, field_name, field_type,
                                 from_value, to_value, from_string, to_string)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', tuple(changelog_data.values()))
                            
                            total_changelog_items += 1
                            
                    except Exception as e:
                        logger.error(f"Error saving changelog for {issue_key}: {e}")
                        continue
            
            conn.commit()
            logger.info(f"Saved {total_changelog_items} changelog items for {len(changelogs_dict)} issues")


class JsonProcessor:
    """Processes and formats issue data for JSON output."""
    
    @staticmethod
    def _extract_text_from_adf(adf_content: Dict) -> str:
        """Extract plain text from Atlassian Document Format (ADF)."""
        if not isinstance(adf_content, dict):
            return str(adf_content)
        
        text_parts = []
        
        def extract_text_recursive(node):
            if isinstance(node, dict):
                if node.get('type') == 'text':
                    text_parts.append(node.get('text', ''))
                elif 'content' in node:
                    for child in node['content']:
                        extract_text_recursive(child)
                elif 'text' in node:
                    text_parts.append(node['text'])
            elif isinstance(node, list):
                for item in node:
                    extract_text_recursive(item)
        
        extract_text_recursive(adf_content)
        return ' '.join(text_parts).strip()
    
    @staticmethod 
    def process_issues_to_json(issues: List[Dict], changelogs_dict: Dict[str, List[Dict]]) -> List[Dict]:
        """Process raw JIRA issues and changelogs into JSON format."""
        processed_issues = []
        
        for issue in issues:
            fields = issue['fields']
            
            # Extract URLs
            api_url = issue.get('self', '')
            # Construct web UI URL from the API URL or issue key
            web_url = ''
            if api_url:
                # Extract base URL from API URL and construct browse URL
                # API URL format: https://domain.atlassian.net/rest/api/3/issue/12345
                # Web URL format: https://domain.atlassian.net/browse/ISSUE-KEY
                base_url = api_url.split('/rest/api/')[0] if '/rest/api/' in api_url else ''
                if base_url:
                    web_url = f"{base_url}/browse/{issue['key']}"
            
            # Extract story points from customfield_10026
            story_points = None
            story_points_field = 'customfield_10026'
            if story_points_field in fields:
                story_points = fields[story_points_field]
            
            # Extract parent issue key (for subtasks)
            parent_key = None
            if 'parent' in fields and fields['parent']:
                parent_key = fields['parent'].get('key')
            
            # Extract epic information (try common custom field IDs)
            epic_key = None
            epic_name = None
            epic_fields = ['customfield_10014', 'customfield_10008', 'customfield_10002']
            for epic_field in epic_fields:
                if epic_field in fields and fields[epic_field]:
                    epic_data = fields[epic_field]
                    if isinstance(epic_data, dict):
                        epic_key = epic_data.get('key')
                        epic_name = epic_data.get('name') or epic_data.get('summary')
                    elif isinstance(epic_data, str):
                        epic_key = epic_data
                    break
            
            # Extract sprint information (try common custom field IDs)
            sprint_info = []
            sprint_fields = ['customfield_10020', 'customfield_10010', 'customfield_10004']
            for sprint_field in sprint_fields:
                if sprint_field in fields and fields[sprint_field]:
                    sprint_data = fields[sprint_field]
                    if isinstance(sprint_data, list):
                        for sprint in sprint_data:
                            if isinstance(sprint, dict):
                                sprint_info.append({
                                    'id': sprint.get('id'),
                                    'name': sprint.get('name'),
                                    'state': sprint.get('state'),
                                    'start_date': sprint.get('startDate'),
                                    'end_date': sprint.get('endDate'),
                                    'complete_date': sprint.get('completeDate')
                                })
                            elif isinstance(sprint, str):
                                # Sometimes sprint data is a comma-separated string
                                sprint_info.append({'name': sprint})
                    elif isinstance(sprint_data, dict):
                        sprint_info.append({
                            'id': sprint_data.get('id'),
                            'name': sprint_data.get('name'),
                            'state': sprint_data.get('state'),  
                            'start_date': sprint_data.get('startDate'),
                            'end_date': sprint_data.get('endDate'),
                            'complete_date': sprint_data.get('completeDate')
                        })
                    break
            
            # Extract linked issues
            linked_issues = []
            if 'issuelinks' in fields and fields['issuelinks']:
                for link in fields['issuelinks']:
                    link_data = {
                        'type': link.get('type', {}).get('name', ''),
                        'direction': None,
                        'issue_key': None,
                        'summary': None
                    }
                    
                    # Check if this issue is the inward or outward link
                    if 'inwardIssue' in link:
                        link_data['direction'] = 'inward'
                        link_data['issue_key'] = link['inwardIssue'].get('key')
                        link_data['summary'] = link['inwardIssue'].get('fields', {}).get('summary', '')
                    elif 'outwardIssue' in link:
                        link_data['direction'] = 'outward'  
                        link_data['issue_key'] = link['outwardIssue'].get('key')
                        link_data['summary'] = link['outwardIssue'].get('fields', {}).get('summary', '')
                    
                    if link_data['issue_key']:
                        linked_issues.append(link_data)
            
            # Process description
            description = ''
            if fields.get('description'):
                if isinstance(fields['description'], dict) and 'content' in fields['description']:
                    # Extract text from Atlassian Document Format
                    description = JsonProcessor._extract_text_from_adf(fields['description'])
                else:
                    description = str(fields['description'])
            
            # Build the processed issue
            processed_issue = {
                'id': issue['id'],
                'key': issue['key'],
                'summary': fields.get('summary', ''),
                'description': description,
                'issue_type': fields.get('issuetype', {}).get('name', '') if fields.get('issuetype') else '',
                'status': fields.get('status', {}).get('name', '') if fields.get('status') else '',
                'priority': fields.get('priority', {}).get('name', '') if fields.get('priority') else '',
                'assignee': fields.get('assignee', {}).get('displayName', '') if fields.get('assignee') else '',
                'reporter': fields.get('reporter', {}).get('displayName', '') if fields.get('reporter') else '',
                'created': fields.get('created', ''),
                'updated': fields.get('updated', ''),
                'resolved': fields.get('resolutiondate', ''),
                'project_key': fields.get('project', {}).get('key', '') if fields.get('project') else '',
                'labels': fields.get('labels', []),
                'components': [c.get('name', '') for c in fields.get('components', [])],
                'fix_versions': [v.get('name', '') for v in fields.get('fixVersions', [])],
                'story_points': story_points,
                'parent_key': parent_key,
                'linked_issues': linked_issues,
                'epic_key': epic_key,
                'epic_name': epic_name,
                'sprint_info': sprint_info,
                'changelogs': [],
                'api_url': api_url,
                'web_url': web_url
            }
            
            # Add changelogs if available
            issue_key = issue['key']
            if issue_key in changelogs_dict:
                processed_changelogs = []
                for changelog in changelogs_dict[issue_key]:
                    for item in changelog.get('items', []):
                        processed_changelogs.append({
                            'change_id': changelog.get('id'),
                            'author': changelog.get('author', {}).get('displayName', '') if changelog.get('author') else '',
                            'created': changelog.get('created', ''),
                            'field_name': item.get('field', ''),
                            'field_type': item.get('fieldtype', ''),
                            'from_value': item.get('from', ''),
                            'to_value': item.get('to', ''),
                            'from_string': item.get('fromString', ''),
                            'to_string': item.get('toString', '')
                        })
                processed_issue['changelogs'] = processed_changelogs
            
            processed_issues.append(processed_issue)
        
        return processed_issues


@click.command()
@click.option('--jira-url', help='JIRA instance URL (e.g., https://your-company.atlassian.net)')
@click.option('--username', help='JIRA username/email')
@click.option('--api-token', help='JIRA API token')
@click.option('--jql', required=True, help='JQL query to filter issues')
@click.option('--output', help='Output file path (if not specified, prints to stdout)')
@click.option('--format', 'output_format', type=click.Choice(['json', 'sqlite']), default='json', help='Output format: json (default) or sqlite')
@click.option('--database', help='SQLite database file path (only used with --format sqlite)')
@click.option('--max-results', default=-1, help='Maximum number of issues to fetch (use 0 or -1 for unlimited)')
@click.option('--include-changelogs/--no-changelogs', default=True, help='Include issue changelogs')
@click.option('--max-workers', default=10, help='Maximum number of concurrent workers for changelog fetching')
@click.option('--pretty/--no-pretty', default=True, help='Pretty-print JSON output (only for JSON format)')
def main(jira_url: str, username: str, api_token: str, jql: str, output: Optional[str], 
         output_format: str, database: Optional[str], max_results: int, include_changelogs: bool, 
         max_workers: int, pretty: bool):
    """Extract JIRA issues and changelogs. Output to JSON (default) or SQLite database."""
    
    try:
        # Get credentials from environment if not provided via CLI
        jira_url = jira_url or os.getenv('JIRA_URL')
        username = username or os.getenv('JIRA_USERNAME')
        api_token = api_token or os.getenv('JIRA_API_TOKEN')
        
        # Validate required parameters
        if not jira_url:
            raise click.ClickException("JIRA URL is required. Provide via --jira-url or JIRA_URL environment variable.")
        if not username:
            raise click.ClickException("Username is required. Provide via --username or JIRA_USERNAME environment variable.")
        if not api_token:
            raise click.ClickException("API token is required. Provide via --api-token or JIRA_API_TOKEN environment variable.")
        
        # Validate format-specific parameters
        if output_format == 'sqlite' and not database:
            database = 'jira_data.db'  # Default database name
            
        # Initialize extractor
        extractor = JiraExtractor(jira_url, username, api_token, max_workers)
        
        # Fetch issues
        logger.info(f"Executing JQL query: {jql}")
        issues = extractor.get_issues(jql, max_results)
        
        if not issues:
            logger.warning("No issues found matching the JQL query")
            return
        
        logger.info("Extracting story points, parent issues, linked issues, epics, and sprint information")
        
        # Fetch changelogs if requested
        changelogs_dict = {}
        if include_changelogs:
            logger.info(f"Fetching changelogs for {len(issues)} issues using {max_workers} concurrent workers...")
            issue_keys = [issue['key'] for issue in issues]
            changelogs_dict = extractor.get_changelogs_batch(issue_keys)
        
        # Output based on format
        if output_format == 'sqlite':
            # SQLite output
            db_manager = DatabaseManager(database)
            db_manager.save_issues(issues)
            
            if include_changelogs:
                db_manager.save_changelogs_batch(changelogs_dict)
            
            logger.info(f"Extraction completed. Data saved to {database}")
            
        else:
            # JSON output (default)
            processed_issues = JsonProcessor.process_issues_to_json(issues, changelogs_dict)
            
            # Format JSON output
            if pretty:
                json_output = json.dumps(processed_issues, indent=2, ensure_ascii=False)
            else:
                json_output = json.dumps(processed_issues, ensure_ascii=False)
            
            # Output to file or stdout
            if output:
                with open(output, 'w', encoding='utf-8') as f:
                    f.write(json_output)
                logger.info(f"JSON data exported to {output}")
            else:
                print(json_output)
            
            logger.info(f"Extraction completed. Processed {len(processed_issues)} issues")
        
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        raise click.ClickException(str(e))


if __name__ == '__main__':
    main() 