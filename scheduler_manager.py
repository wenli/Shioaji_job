from apscheduler.schedulers.background import BackgroundScheduler
import logging
import os
from download_futures_data import sync_to_latest

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = BackgroundScheduler()

def start_scheduler(api):
    """Starts the APScheduler with automatic K-line sync job.

    Args:
        api (shioaji.Shioaji): Logged in client.
    """
    if scheduler.running:
        logger.warning("Scheduler is already running.")
        return
        
    logger.info("Starting Background Scheduler...")

    # Fetch target code from environment, default to TXFR1 (Near month continuous)
    target_code = os.getenv("TARGET_CODE", "TXFR1")
    
    try:
        # Resolving Contract
        # For continuous contract, Shioaji standard: api.Contracts.Futures.TXF.TXFR1
        if hasattr(api.Contracts.Futures, 'TXF') and hasattr(api.Contracts.Futures.TXF, target_code):
             contract = getattr(api.Contracts.Futures.TXF, target_code)
        else:
             # Fallback/Default for testing
             contract = api.Contracts.Futures.TXF.TXFR1
             
        logger.info(f"Adding sync job for {contract.code}...")

        # Add job: every minute at 5th second offset
        # avoids updating exactly at 00 second when bar is closing
        scheduler.add_job(
            sync_to_latest,
            'cron',
            minute='*',
            second='5',
            args=[api, contract],
            id="sync_kbars_job",
            replace_existing=True
        )
        
        # Add a test job or just start
        scheduler.start()
        logger.info("Scheduler started successfully.")
        
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

def stop_scheduler():
    """Stops the scheduler safely."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped.")
