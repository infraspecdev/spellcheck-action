import openai
import os
import sys
import logging
import json
import requests

class Logger:
    @staticmethod
    def configure(log_level=None):
        log_level = log_level or os.getenv("LOG_LEVEL", "ERROR").upper()
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        logging.basicConfig(level=levels.get(log_level, logging.ERROR),
                            format='%(asctime)s - %(levelname)s - %(message)s')

class FileHandler:
    @staticmethod
    def read_file(file_path):
        try:
            with open(file_path, 'r') as file:
                return file.readlines()
        except Exception as e:
            logging.error(f"Error reading file {file_path}: {e}")
            return None

class SpellChecker:
    def __init__(self, config):
        self.config = config
        openai.api_key = config['api_key'] 

    def check_spelling_with_line_numbers(self, numbered_content):
        try:
            response = openai.ChatCompletion.create(
                model=config['model'],
                messages=[
                    {"role": "system", "content": "You are a helpful assistant checking spelling and grammar."},
                    {"role": "user", "content": (
                        "You are a helpful assistant that checks and corrects only spelling and grammar issues in markdown files, "
                        "without altering any other content such as indentation, line numbers, or formatting.\n"
                        "For each line provided, identify the specific word with the issue and provide its correction.\n"
                        "Return a JSON object with the following fields only if the category is not 'none':\n"
                        "- original_text: contains only the specific word in the line that has a spelling or grammar issue\n"
                        "- suggested_text: contains the corrected word\n"
                        "- line_number: the exact line number of the original md file\n"
                        "- category: either 'spelling issue', 'grammar issue', or 'both'\n\n"
                        "Only include entries where the category is 'spelling issue', 'grammar issue', or 'both'.\n"
                        "If all lines are correct, return a single object in a list with the message: 'everything looks good to me 🎉'.\n\n"
                        "Here are the lines:\n"
                        f"{''.join(numbered_content)}"
                    )}
                ],
                max_tokens=int(config['max_tokens'])
            )
            return response['choices'][0]['message']['content']
        except Exception as e:
            logging.error(f"Error during OpenAI API request: {e}")
            return None

class GitHubPRCommenter:
    def __init__(self, repo, pr_number, token):
        self.repo = repo
        self.pr_number = pr_number
        self.token = token
        self.api_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}/comments"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def post_comment(self, file_path, line_number, message):
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
        response = requests.post(self.api_url, headers=self.headers, json=data)
        if response.status_code == 201:
            logging.info(f"Successfully posted comment to {file_path} on line {line_number}.")
        else:
            logging.error(f"Failed to post comment: {response.status_code} - {response.text}")

    def get_latest_commit(self):
        pr_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}"
        response = requests.get(pr_url, headers=self.headers)
        if response.status_code == 200:
            return response.json()['head']['sha']
        else:
            logging.error(f"Failed to get latest commit SHA: {response.status_code} - {response.text}")
            return None

    def delete_existing_comments(self):
        comments_url = f"https://api.github.com/repos/{self.repo}/pulls/{self.pr_number}/comments"
        response = requests.get(comments_url, headers=self.headers)
        if response.status_code == 200:
            for comment in response.json():
                delete_url = f"https://api.github.com/repos/{self.repo}/pulls/comments/{comment['id']}"
                delete_response = requests.delete(delete_url, headers=self.headers)
                if delete_response.status_code == 204:
                    logging.info(f"Deleted comment ID {comment['id']}.")
                else:
                    logging.error(f"Failed to delete comment ID {comment['id']}: {delete_response.status_code} - {delete_response.text}")
        else:
            logging.error(f"Failed to get comments: {response.status_code} - {response.text}")

class SpellCheckProcessor:
    def __init__(self, config):
        self.config = config
        self.spell_checker = SpellChecker(config.openai)
        self.has_issues = False
        self.commenter = GitHubPRCommenter(self.config.github['repository'], self.config.github['pr_number'], self.config.github['token'])

    def inject_line_numbers(self, lines):
        return [f"{idx + 1}: {line.rstrip()}\n" for idx, line in enumerate(lines)]

    def process_files(self):
        self.commenter.delete_existing_comments()
        for file_path in self.config.github['files']:
            file_lines = FileHandler.read_file(file_path)
            if file_lines:
                numbered_content = self.inject_line_numbers(file_lines)
                result = self.spell_checker.check_spelling_with_line_numbers(numbered_content)
                if result:
                    self.post_inline_comments(result, file_path)
            else:
                logging.error(f"Skipping file {file_path} due to read error.")
        self.check_pr_status()

    def check_pr_status(self):
        if self.has_issues:
            sys.exit(1)
        else:
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
                elif category == "both" and self.config.spell_check["failOnBoth"]:
                    self.has_issues = True
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON: {e}")

def main():
    Logger.configure()
    config = Config()
    processor = SpellCheckProcessor(config)
    processor.process_files()

if __name__ == "__main__":
    main()


class Config:
    def __init__(self):
        self.github = {
            "repository": os.getenv("INPUT_GITHUB_REPOSITORY"),
            "token": os.getenv("INPUT_GITHUB_TOKEN"),
            "pr_number": os.getenv("INPUT_PR_NUMBER"),
            "files": os.getenv("INPUT_FILES").split(',')
        }
        self.spell_check = {
            "failOnSpelling": os.getenv("INPUT_FAIL_ON_SPELLING"),
            "failOnGrammar": os.getenv("INPUT_FAIL_ON_GRAMMAR"),
            "failOnBoth": os.getenv("INPUT_FAIL_ON_BOTH")
        }
        self.openai = {
            "api_key": os.getenv("INPUT_OPENAI_API_KEY"),
            "model": os.getenv("INPUT_OPENAI_MODEL"),
            "max_tokens": int(os.getenv("INPUT_MODEL_MAX_TOKEN"))
        }
        self.validate()

    def validate(self):
        if not all(self.github.values()):
            logging.error("GITHUB_REPOSITORY, GITHUB_TOKEN, PR_NUMBER, and INPUT_FILES must be set as environment variables.")
            sys.exit(1)
        if not all(self.spell_check.values()):
            logging.error("FAIL_ON_SPELLING, FAIL_ON_GRAMMAR & FAIL_ON_BOTH must be set as environment variables.")
            sys.exit(1)
        if not all(self.openai.values()):
            logging.error("OPENAI_API_KEY, OPENAI_MODEL & MODEL_MAX_TOKEN must be set as environment variables.")
            sys.exit(1)