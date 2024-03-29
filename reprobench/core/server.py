from pathlib import Path

import click
import gevent
import zmq.green as zmq
from loguru import logger
from reprobench.console.decorators import common, server_info
from reprobench.core.bootstrap.server import bootstrap
from reprobench.core.db import Observer
from reprobench.core.events import BOOTSTRAP
from reprobench.core.observers import CoreObserver
from reprobench.utils import decode_message, get_db_path, import_class, init_db


class BenchmarkServer(object):
    BACKEND_ADDRESS = "inproc://backend"

    def __init__(self, output_dir, frontend_address, **kwargs):
        db_path = get_db_path(output_dir)
        init_db(db_path)
        self.bootstrapped = Path(db_path).exists()
        self.frontend_address = frontend_address
        self.observers = [CoreObserver]

    def receive_event(self):
        address, event_type, payload = self.frontend.recv_multipart()
        logger.trace((address, event_type, decode_message(payload)))
        return address, event_type, payload

    def wait_for_bootstrap(self):
        while True:
            address, event_type, payload = self.receive_event()
            if event_type == BOOTSTRAP:
                break

        payload = decode_message(payload)
        bootstrap(**payload)
        self.bootstrapped = True
        self.frontend.send_multipart([address, b"done"])

    def loop(self):
        while True:
            address, event_type, payload = self.receive_event()
            self.backend.send_multipart([event_type, payload, address])

    def run(self):
        self.context = zmq.Context()
        self.frontend = self.context.socket(zmq.ROUTER)
        self.frontend.bind(self.frontend_address)
        self.backend = self.context.socket(zmq.PUB)
        self.backend.bind(self.BACKEND_ADDRESS)

        logger.info(f"Listening on {self.frontend_address}...")

        if not self.bootstrapped:
            logger.info(f"Waiting for bootstrap event...")
            self.wait_for_bootstrap()

        self.observers += [
            import_class(o.module) for o in Observer.select(Observer.module)
        ]

        observer_greenlets = []
        for observer in self.observers:
            greenlet = gevent.spawn(
                observer.observe,
                self.context,
                backend_address=self.BACKEND_ADDRESS,
                reply=self.frontend,
            )
            observer_greenlets.append(greenlet)

        serverlet = gevent.spawn(self.loop)
        logger.info(f"Ready to receive events...")
        serverlet.join()

        gevent.killall(observer_greenlets)


@click.command(name="server")
@click.option(
    "-d", "--output-dir", type=click.Path(), default="./output", show_default=True
)
@server_info
@common
def cli(server_address, output_dir, **kwargs):
    server = BenchmarkServer(output_dir, server_address, **kwargs)
    server.run()


if __name__ == "__main__":
    cli()
