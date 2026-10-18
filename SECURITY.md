# Security Policy

MeshDeckWeb controls a Meshtastic node: anyone who gets past the login can
send messages and change the node's configuration. Security reports are
welcome and taken seriously.

## Supported versions

Only the latest release gets security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub instead: open the repository's
**Security** tab and choose **Report a vulnerability**. Include:

- the MeshDeckWeb version (`APP_VERSION` in `constants.py`)
- what an attacker can do, and from where (same network, internet, …)
- the steps to reproduce it

You should get an answer within a week. Once a fix is released, the report
is credited in the CHANGELOG, unless you prefer to stay anonymous.

## Deployment notes

MeshDeckWeb is designed for a home or field network, not for direct exposure
to the internet. To reach it from outside, use a VPN such as Tailscale or
WireGuard rather than forwarding the port on your router.
