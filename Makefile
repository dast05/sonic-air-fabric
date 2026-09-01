.PHONY: install test render discover provision deploy stop
install:
	python3 -m pip install -r requirements.txt
test:
	python3 -m pytest -q --junitxml=build/tests.xml
render:
	python3 -m fabriclab render
discover provision deploy stop:
	python3 -m fabriclab $@
