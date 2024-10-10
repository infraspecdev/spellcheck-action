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

    @staticmethod
    def save_result(file_path, result):
        try:
            with open("spell_check_result_with_lines.json", "a") as result_file:
                result = result.strip('```json\n```')
                result_file.write(result + "\n")
        except Exception as e:
            logging.error(f"Error saving result for {file_path}: {e}")

class SpellChecker:
    def __init__(self):
        self.api_key = os.getenv("INPUT_OPENAI_API_KEY")
        if not self.api_key:
            logging.error("OPENAI_API_KEY environment variable not set.")
            sys.exit(1)
        openai.api_key = self.api_key

    def check_spelling_with_line_numbers(self, numbered_content):
        try:
            response = openai.ChatCompletion.create(
                model=os.getenv("INPUT_OPENAI_MODEL"),
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
                max_tokens=os.getenv("INPUT_MODEL_MAX_TOKEN")
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
    def __init__(self, file_paths):
        self.file_paths = file_paths
        self.spell_checker = SpellChecker()
        self.repo = os.getenv("INPUT_GITHUB_REPOSITORY")
        self.pr_number = os.getenv("INPUT_PR_NUMBER")
        self.token = os.getenv("INPUT_GITHUB_TOKEN")
        self.has_issues = False
        if not all([self.repo, self.pr_number, self.token]):
            logging.error("GITHUB_REPOSITORY, PR_NUMBER, and GITHUB_TOKEN must be set as environment variables.")
            sys.exit(1)
        self.commenter = GitHubPRCommenter(self.repo, self.pr_number, self.token)

    def inject_line_numbers(self, lines):
        return [f"{idx + 1}: {line.rstrip()}\n" for idx, line in enumerate(lines)]

    def process_files(self):
        self.commenter.delete_existing_comments()
        for file_path in self.file_paths:
            file_lines = FileHandler.read_file(file_path)
            if file_lines:
                numbered_content = self.inject_line_numbers(file_lines)
                result = self.spell_checker.check_spelling_with_line_numbers(numbered_content)
                if result:
                    FileHandler.save_result(file_path, result)
                    issues_found = self.post_inline_comments(result, file_path)
                    if issues_found:
                        self.has_issues = True
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
            if isinstance(result_json, list):
                for entry in result_json:
                    if "message" in entry:
                        continue
                    line_number = entry.get("line_number")
                    category = entry.get("category")
                    original_text = entry.get("original_text")
                    suggested_text = entry.get("suggested_text")
                    if category in ["spelling issue", "both"]:
                        self.has_issues = True
                        message = f"**{category.capitalize()}**: `{original_text}`\n**Suggestion**: `{suggested_text}`"
                        self.commenter.post_comment(file_path, line_number, message)
                    elif category == "grammar issue":
                        message = f"**{category.capitalize()}**: `{original_text}`\n**Suggestion**: `{suggested_text}`"
                        self.commenter.post_comment(file_path, line_number, message)
                return self.has_issues
            else:
                return False
        except json.JSONDecodeError:
            return False

def main():
    Logger.configure()
    input_files = os.getenv("INPUT_FILES").split(',')
    processor = SpellCheckProcessor(input_files)
    processor.process_files()

if __name__ == "__main__":
    main()
