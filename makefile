.PHONY: install test format lint clean
	
install:
	pip install -r requirements.txt
	
test:
	pytest tests/ -v
	
test-cov:
	pytest tests/ --cov=src --cov-report=html

format:
	black src/ tests/

lint:
	ruff check src/ tests/
	mypy src/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov/

run-scanner:
	python -m src.cli.main scan

run-trader:
	python -m src.cli.main trade