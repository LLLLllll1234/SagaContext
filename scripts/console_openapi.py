"""Export the read console API schema without starting the application lifespan."""
import json
from sagacontext.daemon import create_app


def schema():
    document = create_app().openapi()
    document['paths'] = {path: value for path, value in document['paths'].items()
                         if path.startswith('/console/v1/')}
    # Keep only definitions reachable from console routes.
    definitions = document['components']['schemas']
    required = set()
    def walk(value):
        if isinstance(value, dict):
            reference = value.get('$ref', '').removeprefix('#/components/schemas/')
            if reference and reference not in required:
                required.add(reference)
                walk(definitions[reference])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(document['paths'])
    document['components']['schemas'] = {name: value for name, value in definitions.items() if name in required}
    return document


if __name__ == '__main__':
    print(json.dumps(schema(), ensure_ascii=False))
