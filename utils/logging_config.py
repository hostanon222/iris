import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
os.makedirs('logs', exist_ok=True)

# Configure logging
def setup_logging():
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
    )

    # Create handlers
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Create rotating file handler
    log_file = f'logs/iris_{datetime.now().strftime("%Y%m%d")}.log'
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)

    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Create specific loggers
    loggers = {
        'iris': logging.getLogger('iris'),
        'art_gen': logging.getLogger('art_gen'),
        'websocket': logging.getLogger('websocket'),
        'gallery': logging.getLogger('gallery')
    }

    for logger in loggers.values():
        logger.setLevel(logging.INFO)

    return loggers

# Initialize loggers
loggers = setup_logging() 