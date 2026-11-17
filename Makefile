install:
	pip install -e ".[dev,deploy,netbox]"

lint:
	ruff check .
	ruff format --check .
	yamllint .

test:
	pytest -q

validate:
	netauto validate

render:
	netauto render

# Everything CI runs, in the same order.
ci: lint validate
	netauto render --check
	pytest -q

lab-up:
	sudo containerlab deploy -t clab/lab.clab.yml

lab-down:
	sudo containerlab destroy -t clab/lab.clab.yml --cleanup

check:
	netauto check

dry-run:
	netauto deploy

deploy:
	netauto deploy --commit

.PHONY: install lint test validate render ci lab-up lab-down check dry-run deploy
