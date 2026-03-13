import asyncio
import logging
import re
from dataclasses import dataclass

import httpx


@dataclass
class TunnelInfo:
    """
    Holds the public URL and process handle for a Cloudflare quick tunnel.

    Attributes:
        public_url (str): The ``*.trycloudflare.com`` URL.
        process (asyncio.subprocess.Process): The ``cloudflared`` subprocess.
    """

    public_url: str
    process: asyncio.subprocess.Process


TUNNEL_URL_PATTERN = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")
TUNNEL_STARTUP_TIMEOUT = 30.0


async def start_tunnel(local_port: int) -> TunnelInfo:
    """
    Launch a Cloudflare quick tunnel and return its public URL.

    Starts ``cloudflared tunnel --url http://localhost:{port}`` as an asyncio
    subprocess and parses the ``*.trycloudflare.com`` URL from stderr.

    Args:
        local_port (int): The local port to tunnel traffic to.

    Returns:
        TunnelInfo: The tunnel's public URL and process handle.

    Raises:
        TimeoutError: If the tunnel URL is not found within the startup timeout.
        RuntimeError: If the cloudflared process exits before producing a URL.
    """

    process = await asyncio.create_subprocess_exec(
        "cloudflared",
        "tunnel",
        "--url",
        f"http://localhost:{local_port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    assert process.stderr is not None

    elapsed = 0.0
    interval = 0.5

    while elapsed < TUNNEL_STARTUP_TIMEOUT:
        if process.returncode is not None:
            stderr_output = await process.stderr.read()

            raise RuntimeError(
                f"cloudflared exited with code {process.returncode}: "
                f"{stderr_output.decode()}"
            )

        try:
            line = await asyncio.wait_for(
                process.stderr.readline(),
                timeout=interval,
            )
        except TimeoutError:
            elapsed += interval
            continue

        decoded = line.decode()
        match = TUNNEL_URL_PATTERN.search(decoded)

        if match:
            return TunnelInfo(public_url=match.group(1), process=process)

        elapsed += interval

    process.terminate()

    raise TimeoutError(
        f"Cloudflare tunnel URL not found after {TUNNEL_STARTUP_TIMEOUT}s"
    )


TUNNEL_READY_TIMEOUT = 60.0
TUNNEL_READY_INTERVAL = 1.0

logger: logging.Logger = logging.getLogger(__name__)


async def wait_for_tunnel_ready(public_url: str) -> None:
    """
    Poll the health endpoint through the tunnel until it responds.

    Verifies full connectivity (internet -> Cloudflare -> tunnel ->
    local server) before triggering webhook events.

    Args:
        public_url (str): The tunnel's public URL.

    Raises:
        TimeoutError: If the health endpoint does not respond within the timeout.
    """

    elapsed = 0.0

    async with httpx.AsyncClient(timeout=5.0) as client:
        while elapsed < TUNNEL_READY_TIMEOUT:
            try:
                response = await client.get(f"{public_url}/health")

                if response.status_code == 200:
                    logger.info("Tunnel ready at %s", public_url)

                    return
            except httpx.HTTPError:
                pass

            await asyncio.sleep(TUNNEL_READY_INTERVAL)
            elapsed += TUNNEL_READY_INTERVAL

    raise TimeoutError(
        f"Tunnel health check not reachable after {TUNNEL_READY_TIMEOUT}s",
    )


async def stop_tunnel(tunnel: TunnelInfo) -> None:
    """
    Stop a running Cloudflare tunnel.

    Sends SIGTERM and waits up to 5 seconds. If the process does not exit,
    sends SIGKILL.

    Args:
        tunnel (TunnelInfo): The tunnel to stop.
    """

    if tunnel.process.returncode is not None:
        return

    tunnel.process.terminate()

    try:
        await asyncio.wait_for(tunnel.process.wait(), timeout=5.0)
    except TimeoutError:
        tunnel.process.kill()
        await tunnel.process.wait()
