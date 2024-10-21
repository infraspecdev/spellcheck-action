"""Spell Check Action Module.

This module provides functionality for checking spelling and grammar in
markdown files using OpenAI API and posting comments on GitHub pull requests.
"""

import os
import sys
import logging
import json
import requests
import openai

class Config:
    """Configuration class to read and validate environment variables."""

    def __init__(self):
        self.github = {
            "repository": os.getenv("INPUT_GITHUB_REPOSITORY"),
            "token": os.getenv("INPUT_GITHUB_TOKEN"),
            "pr_number": os.getenv("INPUT_PR_NUMBER"),
            "files": os.getenv("INPUT_FILES").split(',')
        }
        self.spell_check = {
            "failOnSpelling": self.str_to_bool(os.getenv("INPUT_FAIL_ON_SPELLING")),
            "failOnGrammar": self.str_to_bool(os.getenv("INPUT_FAIL_ON_GRAMMAR")),
            "default_language": os.getenv("INPUT_DEFAULT_LANGUAGE")
        }
        self.openai = {
            "api_key": os.getenv("INPUT_OPENAI_API_KEY"),
            "model": os.getenv("INPUT_OPENAI_MODEL"),
            "max_tokens": int(os.getenv("INPUT_MODEL_MAX_TOKEN"))
        }
        self.log = {
            "log_level": os.getenv("INPUT_LOG_LEVEL")
        }
        self.validate()

    def str_to_bool(self, value):
        """Convert string to boolean."""
        if value is None:
            return False
        return value.lower() == "true"

    def validate(self):
        """Validate required environment variables."""
        if not all(self.github.values()):
            logging.error(
                "GITHUB_REPOSITORY, GITHUB_TOKEN, PR_NUMBER, and INPUT_FILES must be set."
            )
            sys.exit(1)
        if not all(self.openai.values()):
            logging.error(
                "OPENAI_API_KEY, OPENAI_MODEL & MODEL_MAX_TOKEN must be set."
            )
            sys.exit(1)


class Logger:
    """Logger class to configure logging settings."""

    def __init__(self, config):
        self.config = config
        self.configure()

    def configure(self):
        """Configure logging based on log level."""
        log_level = self.config.log['log_level']
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        logging.basicConfig(
            level=levels.get(log_level, logging.ERROR),
            format='%(asctime)s - %(levelname)s - %(message)s'
        )


class FileHandler:
    """FileHandler class to read files."""

    @staticmethod
    def read_file(file_path):
        """Read content from a file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.readlines()
        except OSError as e:
            logging.error("Error reading file %s: %s", file_path, e)
            return None


class SpellChecker:
    """SpellChecker class to check spelling and grammar."""

    def __init__(self, config):
        self.config = config
        openai.api_key = config.openai['api_key']

    def check_spelling_with_line_numbers(self, numbered_content):
        """Check spelling and grammar using OpenAI API."""
        try:
            response = openai.ChatCompletion.create(
                model=self.config.openai['model'],
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant checking spelling and grammar."
                        )
                    },
                    {
                        "role": "user",
                        "content": (
                            "You are a helpful assistant that checks and corrects only spelling "
                            "and grammar issues in markdown files, without altering any other "
                            "content such as indentation, line numbers, or formatting.\n"
                            f"Assume the default language is {self.config.spell_check['default_language']}.\n"
                            "For each line provided, identify the specific word with the issue "
                            "and provide its correction.\n"
                            "Return a JSON object with the following fields only if the category "
                            "is not 'none':\n"
                            "- original_text: contains only the specific word in the line that has a "
                            "spelling or grammar issue\n"
                            "- suggested_text: contains the corrected word\n"
                            "- line_number: the exact line number of the original md file\n"
                            "- category: either 'spelling issue', 'grammar issue', or 'both'\n\n"
                            "Only include entries where the category is 'spelling issue', 'grammar "
                            "issue', or 'both'.\n"
                            "If all lines are correct, return a single object in a list with the message: "
                            "'everything looks good to me 🎉'.\n\n"
                            "Here are the lines:\n"
                            f"{''.join(numbered_content)}"
                        )
                    }
                ],
                max_tokens=self.config.openai['max_tokens']
            )
            return response['choices'][0]['message']['content']
        except Exception as e:
            logging.error("Error during OpenAI API request: %s", e)
            return None


class GitHubPRCommenter:
    """GitHubPRCommenter class to post comments on PRs."""

    def __init__(self, repo, pr_number, token):
        self.repo = repo
        self.pr_number = pr_number
        self.token = token
        self.api_url = (
            f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}/comments"
        )
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def post_comment(self, file_path, line_number, message):
        """Post a comment on a PR."""
        commit_id = self.get_latest_commit()
        if not commit_id:
            logging.error("Cannot post comment without a valid commit ID.")
            return

        data = {
            "body": message,
            "commit_id": commit_id,
            "path": file_path,
            "side": "RIGHT",
            "line": line_number
        }
        response = requests.post(self.api_url, headers=self.headers, json=data, timeout=10)
        if response.status_code == 201:
            logging.info("Successfully posted comment to %s on line %d.", file_path, line_number)
        else:
            logging.error("Failed to post comment: %s - %s", response.status_code, response.text)

    def get_latest_commit(self):
        """Get the latest commit SHA for the PR."""
        pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"
        response = requests.get(pr_url, headers=self.headers, timeout=10)
        if response.status_code == 200:
            return response.json()['head']['sha']
        logging.error("Failed to get latest commit SHA: %s - %s", response.status_code, response.text)
        return None

    def delete_existing_comments(self):
        """Delete comments made by the bot on the PR."""
        comments_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}/comments"
        response = requests.get(comments_url, headers=self.headers, timeout=10)
        if response.status_code == 200:
            for comment in response.json():
                if comment['user']['login'] == 'github-actions[bot]':
                    delete_url = f"https://api.github.com/repos/{self.repo}/pulls/comments/{comment['id']}"
                    delete_response = requests.delete(delete_url, headers=self.headers, timeout=10)
                    if delete_response.status_code == 204:
                        logging.info("Deleted comment ID %d by github-actions[bot].", comment['id'])
                    else:
                        logging.error("Failed to delete comment ID %d: %s - %s", comment['id'], delete_response.status_code, delete_response.text)
        else:
            logging.error("Failed to get comments: %s - %s", response.status_code, response.text)


class SpellCheckProcessor:
    """SpellCheckProcessor class to handle the spell-checking process."""

    def __init__(self, config):
        self.config = config
        self.spell_checker = SpellChecker(config)
        self.has_issues = False
        self.commenter = GitHubPRCommenter(
            self.config.github['repository'],
            self.config.github['pr_number'],
            self.config.github['token']
        )

    def inject_line_numbers(self, lines):
        """Inject line numbers into file content."""
        return [f"{idx + 1}: {line.rstrip()}\n" for idx, line in enumerate(lines)]

    def process_files(self):
        """Process files to check for spelling and grammar issues."""
        self.commenter.delete_existing_comments()
        for file_path in self.config.github['files']:
            file_lines = FileHandler.read_file(file_path)
            if file_lines:
                numbered_content = self.inject_line_numbers(file_lines)
                result = self.spell_checker.check_spelling_with_line_numbers(numbered_content)
                if result:
                    self.post_inline_comments(result, file_path)
            else:
                logging.error("Skipping file %s due to read error.", file_path)
        self.check_pr_status()

    def check_pr_status(self):
        """Check the PR status based on found issues."""
        if self.has_issues:
            sys.exit(1)
        sys.exit(0)

    def post_inline_comments(self, result, file_path):
        try:
            result_json = json.loads(result.strip('```json\n```'))
            if not isinstance(result_json, list):
                logging.error("Result is not a list of issues.")
                return
            for entry in result_json:
                if "message" in entry:
                    continue
                line_number = entry.get("line_number")
                category = entry.get("category")
                original_text = entry.get("original_text")
                suggested_text = entry.get("suggested_text")
                message = f"**{category.capitalize()}**: `{original_text}`\n**Suggestion**: `{suggested_text}`"
                self.commenter.post_comment(file_path, line_number, message)

                if category == "spelling issue" and self.config.spell_check["failOnSpelling"]:
                    self.has_issues = True
                elif category == "grammar issue" and self.config.spell_check["failOnGrammar"]:
                    self.has_issues = True
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON: {e}")

def main():
    """Main function to execute the spell-checking process."""
    config = Config()
    Logger(config)
    processor = SpellCheckProcessor(config)
    processor.process_files()


if __name__ == "__main__":
    main()
