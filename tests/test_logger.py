"""Tests for the logging system"""

import json
import logging
import pytest

from src.utils.logger import (
    ColoredFormatter,
    JsonFormatter,
    Logger,
    get_logger,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logger_singleton():
    """Reset Logger singleton before each test"""
    Logger._instance = None
    Logger._loggers = {}
    yield
    Logger._instance = None
    Logger._loggers = {}


def _make_record(
    name="test",
    level=logging.INFO,
    msg="test message",
    exc_info=None,
):
    """Helper to create a LogRecord"""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname="/fake/path/module.py",
        lineno=99,
        msg=msg,
        args=(),
        exc_info=exc_info,
        func="test_func",
    )


class TestColoredFormatter:
    """Tests for ColoredFormatter"""

    @pytest.mark.parametrize(
        "level,levelname,color_code",
        [
            (logging.DEBUG, "DEBUG", "\033[36m"),
            (logging.INFO, "INFO", "\033[32m"),
            (logging.WARNING, "WARNING", "\033[33m"),
            (logging.ERROR, "ERROR", "\033[31m"),
            (logging.CRITICAL, "CRITICAL", "\033[35m"),
        ],
    )
    def test_format_applies_color_by_level(self, level, levelname, color_code):
        fmt = ColoredFormatter("%(levelname)s %(message)s")
        record = _make_record(level=level, msg="hello")
        result = fmt.format(record)
        reset = "\033[0m"
        assert f"{color_code}{levelname}{reset} hello" == result

    def test_format_unknown_level_falls_back_to_reset_color(self):
        fmt = ColoredFormatter("%(levelname)s %(message)s")
        record = _make_record(level=100, msg="custom")
        record.levelname = "CUSTOM"
        result = fmt.format(record)
        assert "\033[0mCUSTOM\033[0m custom" == result

    def test_format_preserves_full_format_string(self):
        fmt = ColoredFormatter("%(levelname)-8s | %(name)s | %(message)s")
        record = _make_record(level=logging.ERROR, msg="failure")
        result = fmt.format(record)
        assert "\033[31mERROR\033[0m | test | failure" == result


class TestJsonFormatter:
    """Tests for JsonFormatter"""

    def test_format_basic_record_contains_all_required_fields(self):
        fmt = JsonFormatter(service="test-svc", environment="testing")
        record = _make_record(name="mylogger", level=logging.INFO, msg="hello world")
        result = fmt.format(record)
        data = json.loads(result)

        assert data["level"] == "INFO"
        assert data["logger"] == "mylogger"
        assert data["message"] == "hello world"
        assert data["service"] == "test-svc"
        assert data["environment"] == "testing"
        assert data["module"] == "module"
        assert data["funcName"] == "test_func"
        assert data["lineno"] == 99
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_format_defaults_service_and_environment(self):
        fmt = JsonFormatter()
        record = _make_record()
        result = fmt.format(record)
        data = json.loads(result)
        assert data["service"] == "algoengine"
        assert data["environment"] == "production"

    def test_format_with_exception_includes_traceback(self):
        fmt = JsonFormatter()
        try:
            raise ValueError("something broke")
        except ValueError:
            import sys

            record = _make_record(
                level=logging.ERROR,
                msg="error occurred",
                exc_info=sys.exc_info(),
            )
            result = fmt.format(record)
            data = json.loads(result)

        assert data["level"] == "ERROR"
        assert data["message"] == "error occurred"
        assert "exception" in data
        assert data["exception"] == "something broke"
        assert "traceback" in data
        assert "ValueError" in data["traceback"]

    def test_format_without_exception_excludes_exception_keys(self):
        fmt = JsonFormatter()
        record = _make_record()
        result = fmt.format(record)
        data = json.loads(result)

        assert "exception" not in data
        assert "traceback" not in data

    def test_format_with_extra_fields_merged_into_output(self):
        fmt = JsonFormatter(extra_fields={"component": "orders", "version": "1.0"})
        record = _make_record()
        result = fmt.format(record)
        data = json.loads(result)

        assert data["component"] == "orders"
        assert data["version"] == "1.0"
        assert data["service"] == "algoengine"

    def test_format_with_extra_fields_overrides_at_format_time(self):
        fmt = JsonFormatter(extra_fields={"component": "orders", "version": "1.0"})
        record = _make_record()
        result = fmt.format(record)
        data = json.loads(result)
        assert data["component"] == "orders"

    def test_format_with_all_constructor_params(self):
        fmt = JsonFormatter(
            service="trading-engine",
            environment="staging",
            extra_fields={"region": "us-east", "instance": "i-12345"},
        )
        record = _make_record(level=logging.WARNING, msg="rate limit approaching")
        result = fmt.format(record)
        data = json.loads(result)

        assert data["service"] == "trading-engine"
        assert data["environment"] == "staging"
        assert data["region"] == "us-east"
        assert data["instance"] == "i-12345"
        assert data["level"] == "WARNING"
        assert data["message"] == "rate limit approaching"

    def test_format_with_stack_info(self):
        fmt = JsonFormatter()
        record = _make_record(level=logging.DEBUG)
        record.stack_info = "Frame 1: main.py:10\nFrame 2: utils.py:25"
        result = fmt.format(record)
        data = json.loads(result)

        assert data["stack_info"] == record.stack_info

    def test_format_without_stack_info_excludes_key(self):
        fmt = JsonFormatter()
        record = _make_record()
        result = fmt.format(record)
        data = json.loads(result)

        assert "stack_info" not in data

    def test_format_output_is_valid_json(self):
        fmt = JsonFormatter()
        record = _make_record(msg='message with "quotes" and \\backslashes')
        result = fmt.format(record)
        data = json.loads(result)
        assert data["message"] == 'message with "quotes" and \\backslashes'

    def test_format_exc_info_none_does_not_include_exception(self):
        fmt = JsonFormatter()
        exc_info = (None, None, None)
        record = _make_record(level=logging.ERROR, exc_info=exc_info)
        result = fmt.format(record)
        data = json.loads(result)

        assert "exception" not in data
        assert "traceback" not in data


class TestLogger:
    """Tests for Logger singleton and setup"""

    def test_singleton_returns_same_instance(self):
        a = Logger()
        b = Logger()
        assert a is b

    def test_singleton_preserves_state_after_setup(self):
        logger = Logger()
        logger.setup(log_dir="/tmp/test-state", log_level=logging.DEBUG)
        logger2 = Logger()
        assert logger2._log_level == logging.DEBUG

    def test_setup_with_json_file_output_writes_valid_json(self, tmp_path):
        log_dir = tmp_path / "json_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
            json_output=True,
            service_name="test-svc",
        )
        log = logger.get_logger("test_json")
        log.info("json format message")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1

        content = log_files[0].read_text(encoding="utf-8").strip()
        data = json.loads(content)
        assert data["message"] == "json format message"
        assert data["service"] == "test-svc"
        assert "timestamp" in data

    def test_setup_with_json_error_log_writes_json_on_error(self, tmp_path):
        log_dir = tmp_path / "json_error_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
            json_output=True,
        )
        log = logger.get_logger("test_json_err")
        log.error("json error message")

        error_files = list(log_dir.glob("algoengine_error_*.log"))
        assert len(error_files) >= 1

        content = error_files[0].read_text(encoding="utf-8").strip()
        data = json.loads(content)
        assert data["message"] == "json error message"
        assert data["level"] == "ERROR"

    def test_setup_with_plain_file_output(self, tmp_path):
        log_dir = tmp_path / "plain_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
            json_output=False,
        )
        log = logger.get_logger("test_plain")
        log.warning("plain format warning")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1

        content = log_files[0].read_text(encoding="utf-8")
        assert "plain format warning" in content
        assert "WARNING" in content
        assert "test_plain" in content

    def test_setup_console_output_only(self, tmp_path, capsys):
        log_dir = tmp_path / "console_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=True,
            file_output=False,
        )
        log = logger.get_logger("test_console")
        log.info("console only message")

        captured = capsys.readouterr()
        assert "console only message" in captured.out

    def test_setup_console_and_file_output_both_enabled(self, tmp_path):
        log_dir = tmp_path / "both_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=True,
            file_output=True,
        )
        log = logger.get_logger("test_both")
        log.error("error in both channels")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "error in both channels" in content

    def test_setup_creates_error_log_file(self, tmp_path):
        log_dir = tmp_path / "error_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
        )
        log = logger.get_logger("test_err")
        log.error("critical failure")

        error_files = list(log_dir.glob("algoengine_error_*.log"))
        assert len(error_files) >= 1

        content = error_files[0].read_text(encoding="utf-8")
        assert "critical failure" in content

    def test_setup_error_log_is_empty_when_only_info_logged(self, tmp_path):
        log_dir = tmp_path / "no_error_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
        )
        log = logger.get_logger("test_noerr")
        log.info("just an info message")

        error_files = list(log_dir.glob("algoengine_error_*.log"))
        assert len(error_files) >= 1
        content = error_files[0].read_text(encoding="utf-8")
        assert content == ""

    def test_setup_creates_log_directory_if_missing(self, tmp_path):
        log_dir = tmp_path / "nested" / "deep" / "logdir"
        assert not log_dir.exists()

        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=True,
            console_output=False,
        )
        log = logger.get_logger("test_dir")
        log.info("directory created")

        assert log_dir.exists()
        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1

    def test_setup_with_custom_max_bytes_and_backup_count(self, tmp_path):
        log_dir = tmp_path / "custom_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            max_bytes=5000,
            backup_count=3,
            file_output=True,
            console_output=False,
        )
        log = logger.get_logger("test_custom")
        log.info("custom settings applied")

        assert logger._max_bytes == 5000
        assert logger._backup_count == 3
        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1
        main_logs = [f for f in log_files if "algoengine_error" not in f.name]
        assert len(main_logs) == 1

    def test_get_logger_same_name_returns_same_logger(self):
        logger = Logger()
        logger.setup(file_output=False, console_output=False)
        a = logger.get_logger("order_manager")
        b = logger.get_logger("order_manager")
        assert a is b

    def test_get_logger_different_names_return_different_loggers(self):
        logger = Logger()
        logger.setup(file_output=False, console_output=False)
        a = logger.get_logger("module_a")
        b = logger.get_logger("module_b")
        assert a is not b

    def test_get_logger_name_prefixed_with_algoengine(self):
        logger = Logger()
        logger.setup(file_output=False, console_output=False)
        log = logger.get_logger("child_module")
        assert log.name == "algoengine.child_module"

    def test_setup_without_file_output_does_not_create_log_files(self, tmp_path):
        log_dir = tmp_path / "no_file_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            file_output=False,
            console_output=False,
        )
        log = logger.get_logger("test_nofile")
        log.info("should not write to file")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) == 0


class TestSetupLogging:
    """Tests for setup_logging convenience function"""

    def test_setup_logging_writes_log_file(self, tmp_path):
        log_dir = tmp_path / "setup_func_logs"
        setup_logging(
            log_dir=str(log_dir),
            log_level=logging.DEBUG,
            console_output=False,
            file_output=True,
            json_output=False,
            service_name="setup-test",
        )

        log = get_logger("test_setup")
        log.info("from setup_logging func")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "from setup_logging func" in content

    def test_setup_logging_defaults_to_info_level(self, tmp_path):
        log_dir = tmp_path / "default_logs"
        setup_logging(log_dir=str(log_dir))

        logger_instance = Logger()
        assert logger_instance._log_level == logging.INFO

    def test_setup_logging_json_output(self, tmp_path):
        log_dir = tmp_path / "json_setup_logs"
        setup_logging(
            log_dir=str(log_dir),
            console_output=False,
            file_output=True,
            json_output=True,
            service_name="setup-json",
        )

        log = get_logger("test_json_setup")
        log.warning("json via setup_logging")

        log_files = list(log_dir.glob("algoengine_*.log"))
        assert len(log_files) >= 1
        content = log_files[0].read_text(encoding="utf-8").strip()
        data = json.loads(content)
        assert data["message"] == "json via setup_logging"
        assert data["service"] == "setup-json"


class TestLogMessageFormatting:
    """Tests for log message formatting correctness"""

    def test_file_output_includes_timestamp_and_level(self, tmp_path):
        log_dir = tmp_path / "fmt_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=False,
            file_output=True,
            json_output=False,
        )
        log = logger.get_logger("test_fmt")
        log.info("format check message")

        log_files = list(log_dir.glob("algoengine_*.log"))
        content = log_files[0].read_text(encoding="utf-8")

        assert "INFO" in content
        assert "test_fmt" in content
        assert "format check message" in content

    def test_file_output_includes_funcname_and_lineno(self, tmp_path):
        log_dir = tmp_path / "func_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=False,
            file_output=True,
            log_level=logging.DEBUG,
        )
        log = logger.get_logger("test_funcfmt")
        log.debug("debug with func info")

        log_files = list(log_dir.glob("algoengine_*.log"))
        content = log_files[0].read_text(encoding="utf-8")
        assert "test_funcfmt" in content
        assert "debug with func info" in content

    def test_console_output_contains_color_codes(self, tmp_path, capsys):
        log_dir = tmp_path / "color_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=True,
            file_output=False,
        )
        log = logger.get_logger("test_color")
        log.warning("color warning")

        captured = capsys.readouterr()
        assert "\033[33mWARNING\033[0m" in captured.out
        assert "color warning" in captured.out

    def test_log_level_filtering_respected(self, tmp_path):
        log_dir = tmp_path / "level_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            log_level=logging.WARNING,
            console_output=False,
            file_output=True,
        )
        log = logger.get_logger("test_level")
        log.info("should be filtered out")
        log.warning("should appear")

        log_files = list(log_dir.glob("algoengine_*.log"))
        content = log_files[0].read_text(encoding="utf-8")
        assert "should be filtered out" not in content
        assert "should appear" in content

    def test_multiple_loggers_write_to_same_file(self, tmp_path):
        log_dir = tmp_path / "multi_logs"
        logger = Logger()
        logger.setup(
            log_dir=str(log_dir),
            console_output=False,
            file_output=True,
            json_output=False,
        )
        a = logger.get_logger("module_a")
        b = logger.get_logger("module_b")
        a.info("from module A")
        b.error("from module B")

        log_files = list(log_dir.glob("algoengine_*.log"))
        content = log_files[0].read_text(encoding="utf-8")
        assert "from module A" in content
        assert "from module B" in content
        assert "module_a" in content
        assert "module_b" in content
