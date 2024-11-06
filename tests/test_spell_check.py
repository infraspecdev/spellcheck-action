import unittest
from unittest.mock import patch, MagicMock, mock_open
import logging
from src.spell_check import Logger, FileHandler, SpellChecker, GitHubPRCommenter, SpellCheckProcessor

class TestLogger(unittest.TestCase):
    """Test cases for Logger class"""

    @patch('logging.basicConfig')
    def test_logger_initialization(self, mock_basicconfig):
        """Test Logger initialization with proper logging configuration"""
        mock_config = MagicMock()
        mock_config.log = {'log_level': 'DEBUG'}
        logger = Logger(mock_config)
        mock_basicconfig.assert_called_once_with(
            level=logging.DEBUG,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.assertIsNotNone(logger)


class TestFileHandler(unittest.TestCase):
    """Test cases for FileHandler class"""

    @patch("builtins.open", new_callable=mock_open, read_data="line1\nline2")
    def test_read_file_success(self, mock_file):
        """Test file reading functionality for success"""
        content = FileHandler.read_file("dummy_path")
        mock_file.assert_called_once_with("dummy_path", 'r', encoding='utf-8')
        self.assertEqual(content, ["line1\n", "line2"])

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_read_file_failure(self, mock_file):
        """Test file reading functionality for failure (FileNotFoundError)"""
        content = FileHandler.read_file("dummy_path")
        mock_file.assert_called_once_with("dummy_path", 'r', encoding='utf-8')
        self.assertIsNone(content)


class TestSpellChecker(unittest.TestCase):
    """Test cases for SpellChecker class"""

    @patch('openai.OpenAI')
    def test_check_spelling_with_line_numbers(self, mock_openai):
        """Test spell checking functionality with line numbers"""
        mock_config = MagicMock()
        mock_config.openai = {
            'api_key': 'dummy_key',
            'model': 'text-davinci-003',
            'max_tokens': 100
        }
        spell_checker = SpellChecker(mock_config)

        mock_openai.return_value.chat.completions.create.return_value = {
            'choices': [{
                'message': {
                    'content': '[{"original_text": "speling", "suggested_text": "spelling", '
                               '"line_number": 1, "category": "spelling issue"}]'
                }
            }]
        }

        numbered_content = ["1: speling\n"]
        result = spell_checker.check_spelling_with_line_numbers(numbered_content)
        expected_result = '[{"original_text": "speling", "suggested_text": "spelling", ' \
                          '"line_number": 1, "category": "spelling issue"}]'
        self.assertEqual(result, expected_result)

    @patch('openai.OpenAI', side_effect=Exception('API Error'))
    def test_check_spelling_api_failure(self, mock_openai):
        """Test spell checker handling API failure"""
        mock_config = MagicMock()
        mock_config.openai = {
            'api_key': 'dummy_key',
            'model': 'text-davinci-003',
            'max_tokens': 100
        }
        spell_checker = SpellChecker(mock_config)
        numbered_content = ["1: speling\n"]
        result = spell_checker.check_spelling_with_line_numbers(numbered_content)
        self.assertIsNone(result)


class TestGitHubPRCommenter(unittest.TestCase):
    """Test cases for GitHubPRCommenter class"""

    @patch('requests.post')
    @patch('requests.get')
    def test_post_comment_success(self, mock_get, mock_post):
        """Test successful posting of comment on PR"""
        mock_get.return_value.json.return_value = {'head': {'sha': 'dummy_sha'}}
        mock_get.return_value.status_code = 200
        mock_post.return_value.status_code = 201

        commenter = GitHubPRCommenter("owner/repo", "123", "dummy_token")
        commenter.post_comment("file.md", 1, "Test message")

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[1]['json']['body'], "Test message")

    @patch('requests.post')
    @patch('requests.get')
    def test_post_comment_failure(self, mock_get, mock_post):
        """Test failure in posting comment on PR"""
        mock_get.return_value.json.return_value = {'head': {'sha': 'dummy_sha'}}
        mock_get.return_value.status_code = 200
        mock_post.return_value.status_code = 400

        with self.assertLogs(level='ERROR') as log:
            commenter = GitHubPRCommenter("owner/repo", "123", "dummy_token")
            commenter.post_comment("file.md", 1, "Test message")

            self.assertIn('Failed to post comment', log.output[0])


class TestSpellCheckProcessor(unittest.TestCase):
    """Test cases for SpellCheckProcessor class"""

    @patch('src.spell_check.FileHandler.read_file', return_value=["line1\n", "speling\n"])
    @patch('src.spell_check.SpellChecker.check_spelling_with_line_numbers')
    @patch('src.spell_check.GitHubPRCommenter.post_comment')
    @patch('src.spell_check.GitHubPRCommenter.delete_existing_comments')
    def test_process_files(self, mock_delete_comments, mock_post_comment, mock_check_spelling, mock_read_file):
        """Test spell check processing for files"""
        mock_config = MagicMock()
        mock_config.github = {
            'repository': 'owner/repo',
            'pr_number': '123',
            'token': 'dummy_token',
            'files': ['file1.md']
        }
        mock_check_spelling.return_value = '[{"original_text": "speling", "suggested_text": ' \
                                           '"spelling", "line_number": 2, "category": "spelling issue"}]'
        processor = SpellCheckProcessor(mock_config)

        with self.assertRaises(SystemExit) as cm:
            processor.process_files()
        self.assertEqual(cm.exception.code, 1)
        mock_delete_comments.assert_called_once()
        mock_post_comment.assert_called_once()

    @patch('src.spell_check.FileHandler.read_file', return_value=None)
    @patch('src.spell_check.GitHubPRCommenter.delete_existing_comments')
    def test_process_files_file_read_failure(self, mock_delete_comments, mock_read_file):
        """Test file read failure handling"""
        mock_config = MagicMock()
        mock_config.github = {
            'repository': 'owner/repo',
            'pr_number': '123',
            'token': 'dummy_token',
            'files': ['file1.md']
        }
        processor = SpellCheckProcessor(mock_config)

        with self.assertRaises(SystemExit) as cm:
            processor.process_files()
        self.assertEqual(cm.exception.code, 0)
        mock_delete_comments.assert_called_once()

    @patch('src.spell_check.FileHandler.read_file', return_value=["line1\n", "speling\n"])
    @patch('src.spell_check.SpellChecker.check_spelling_with_line_numbers')
    @patch('src.spell_check.GitHubPRCommenter.post_comment')
    @patch('src.spell_check.GitHubPRCommenter.delete_existing_comments')
    def test_process_files_check_pr_status_exit(self, mock_delete_comments, mock_post_comment, 
                                                mock_check_spelling, mock_read_file):
        """Test PR status exit during spell check process"""
        mock_config = MagicMock()
        mock_config.github = {
            'repository': 'owner/repo',
            'pr_number': '123',
            'token': 'dummy_token',
            'files': ['file1.md']
        }
        mock_check_spelling.return_value = '[{"original_text": "speling", "suggested_text": ' \
                                           '"spelling", "line_number": 2, "category": "spelling issue"}]'

        processor = SpellCheckProcessor(mock_config)

        with patch.object(processor, 'check_pr_status', side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit) as cm:
                processor.process_files()

            self.assertEqual(cm.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
