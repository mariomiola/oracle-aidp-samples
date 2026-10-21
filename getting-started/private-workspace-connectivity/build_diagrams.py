# Copyright (c) 2026 Oracle and/or its affiliates.
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/.
"""Render original conceptual network diagrams. Build dependency: Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
DIAGRAMS = {
    'V01': ('Amazon S3 through customer egress', ['AIDP compute', 'Workspace private\nconnection', 'Customer subnet\nroutes and rules', 'NAT gateway\noutbound HTTPS', 'S3 regional\npublic endpoint'], 'AWS service', ['Illustrative NAT example: the S3 leg uses public internet connectivity.', 'Configure the exact hostname used by the client; no AWS VPC is required for this example.']),
    'V02': ('Customer source restriction through a service gateway', ['AIDP compute', 'Workspace private\nconnection', 'Customer subnet\nservice-CIDR route', 'Service gateway\nsame OCI region', 'OCI service\nsource policy'], 'Regional OCI service', ['The service gateway serves supported services in the same region as the customer VCN.', 'Keep the source restriction unchanged and correlate the operation with independent source/path evidence.']),
    'V03': ('Cross-region Object Storage through customer egress', ['AIDP compute\nregion A', 'Workspace private\nconnection', 'Customer subnet\nregion A', 'NAT gateway\npublic egress', 'Object Storage\nregion B'], 'Remote OCI region', ['This public-endpoint example uses customer egress; the region A service gateway is not a region B route.', 'Verify the client uses region B and validate an independent region A control read.']),
    'V04': ('Service private endpoint connectivity', ['AIDP compute', 'Workspace private\nconnection', 'Customer subnet\nprivate routes', 'Private endpoint\ntarget security', 'Database or\nprivate service'], 'Private destination', ['Use the private descriptor from the service and preserve the client hostname for certificate checking.', 'If another VCN is involved, add the appropriate peering/DRG routes and return path.']),
    'V05': ('Check each selected service hostname and route', ['AIDP compute', 'Workspace private\nconnection', 'Customer subnet\nDNS and security', 'Service-specific\nnetwork route', 'Selected OCI\nservice endpoint'], 'Selected service', ['Repeat with actual service resource hostnames and their own ports/routes.', 'This is a coverage checklist, not a claim that all service zones are automatically configured.']),
    'V06': ('DNS edits with disposable, owned test endpoints', ['AIDP compute', 'Workspace private\nconnection', 'Customer subnet\nexisting routes', 'Approved route\nto test targets', 'Owned endpoint\nA or B'], 'Owned test targets', ['Add A, replace with B, delete the test entry, and reopen the original configuration.', 'Changing a hostname does not create a gateway, open a firewall, or grant service permissions.']),
    'V07': ('Observe DNS changes in the same Spark application', ['Same Spark\napplication', 'Workspace private\nconnection', 'Customer subnet\nunchanged routes', 'Existing route\nand security', 'Owned fixture\nendpoint A or B'], 'Test destination', ['Only attempt a live edit when supported by the deployment; otherwise record the prerequisite as blocked.', 'Record application identity and lifecycle evidence before and after each permitted DNS change.']),
    'V08': ('Preserve an existing database connection', ['Existing Spark\napplication', 'Original workspace\nprivate connection', 'Original subnet\nroutes and rules', 'Original route\nand security', 'Original database\nconnection'], 'Existing destination', ['Keep the original database hostname, wallet/authentication and access policy unchanged.', 'Add/edit/remove only unrelated test hostnames and repeat the original read-only query each time.']),
}

def font(size, bold=False):
    for name in (('DejaVuSans-Bold.ttf', 'Arial Bold.ttf') if bold else ('DejaVuSans.ttf', 'Arial.ttf')):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()

def build(case, data):
    title, labels, destination, notes = data
    image = Image.new('RGB', (1500, 440), 'white')
    d = ImageDraw.Draw(image)
    d.text((30, 24), case + ' - ' + title, fill='#183d4a', font=font(29, True))
    d.text((30, 74), 'Conceptual topology. Arrows show connection initiation; responses use the established connection.', fill='#4e626b', font=font(19))
    for x, width, label, fill in [(15, 560, 'AIDP managed environment', '#eaf1f5'), (595, 560, 'Customer-controlled network', '#e9f3ee'), (1175, 310, destination, '#f5f1e7')]:
        d.rounded_rectangle((x, 120, x+width, 310), radius=12, fill=fill, outline='#cad5d9', width=2)
        d.text((x+16, 133), label, fill='#35545e', font=font(20, True))
    for i, label in enumerate(labels):
        x = 35 + i*290
        d.rounded_rectangle((x, 180, x+250, 285), radius=8, fill='white', outline='#307160', width=2)
        lines = label.split('\n')
        for j, line in enumerate(lines):
            bbox = d.textbbox((0, 0), line, font=font(22, True))
            d.text((x+125-(bbox[2]-bbox[0])/2, 210+j*28), line, fill='#203d43', font=font(22, True))
        if i < 4:
            d.line((x+250, 233, x+286, 233), fill='#307160', width=3)
            d.polygon([(x+286, 233), (x+276, 226), (x+276, 240)], fill='#307160')
    for i, note in enumerate(notes):
        d.text((30, 338+i*31), note, fill='#425b65', font=font(18))
    d.text((30, 413), 'DNS configuration, packet routing and service authorization are separate checks.', fill='#425b65', font=font(16))
    (ROOT / 'assets').mkdir(exist_ok=True)
    image.save(ROOT / 'assets' / (case.lower() + '-network.png'))

if __name__ == '__main__':
    for key, value in DIAGRAMS.items():
        build(key, value)
