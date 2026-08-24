"""Run one end-to-end policy disassembly with the bundled demo PDF."""

import json
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models.pydantic.request import DsRequest
from repositories.oss_repository import oss_upload_pdf_and_get_url
from workflows.factor_disassembly.factor_disassembly_service import FactorDisassemblyService


def build_request(pdf_url: str) -> DsRequest:
    return DsRequest.model_validate({
        "productInfo": {
            "id": 1,
            "orgCode": "DEMO",
            "policyType": "1",
            "groupPolicyNo": None,
            "planList": [{
                "id": 1,
                "planCode": "DEMO_PLAN",
                "planName": "本地体验医疗计划",
                "planVersion": "1.0",
                "clauseCode": "DEMO_CLAUSE",
                "clauseName": "本地体验医疗保险条款",
                "liabilityList": [{
                    "id": 1,
                    "liabCode": "DEMO_LIABILITY",
                    "liabName": "住院医疗保险金",
                }],
            }],
            "fileList": [{
                "id": 1,
                "fileClass": "03",
                "fileFormat": "application/pdf",
                "fileUrl": pdf_url,
                "fileExternalUrl": pdf_url,
                "fileName": "demo_policy.pdf",
            }],
        },
        "transDate": int(time.time() * 1000),
        "transNo": str(uuid.uuid4()),
        "systemCode": "LOCAL_DEMO",
        "policyNo": "local_demo_policy",
        "planIds": [1],
    })


def main() -> int:
    pdf_path = PROJECT_ROOT / "examples" / "demo_policy.pdf"
    output_path = PROJECT_ROOT / "examples" / "demo_result.json"
    if not pdf_path.exists():
        raise FileNotFoundError(f"Demo PDF not found: {pdf_path}")

    pdf_url = oss_upload_pdf_and_get_url(
        str(pdf_path),
        folder="DemoAssets/clausemind-demo-test/local-demo",
    )
    result = FactorDisassemblyService().process_deconstruction(build_request(pdf_url))
    output_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"LOCAL_DEMO_OK result={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
