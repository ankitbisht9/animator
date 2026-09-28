"""Generation jobs: persistence and the asyncio runner (behind interfaces so a queue such as
Arq/Celery/RQ and a SQL store can replace them without touching the pipeline)."""
