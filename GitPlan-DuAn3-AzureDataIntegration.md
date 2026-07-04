# GitPlan — Dự Án 3: Azure Data & Integration Pipeline

> Tài liệu này triển khai chi tiết từ đề xuất trong `Azure-Data-Integration-Pipeline-Proposal.md`, tổ chức theo mô hình **Git Flow rút gọn** tương tự 2 dự án trước (`GitPlan-DuAn1-Ecommerce.md`, `GitPlan-DuAn2-SearchRecommendation.md`) — mỗi thành phần kỹ thuật tách thành 1 feature branch riêng, có User Story, task checklist, và Definition of Done rõ ràng.

## Tổng quan

Dự án dựng 1 pipeline tích hợp dữ liệu chạm đủ các thành phần cốt lõi của Azure Data & Integration Engineer: **ingest → orchestrate (ADF) → transform (Databricks) → event-driven (Service Bus/Function) → expose (FastAPI) → gateway (APIM)**, có CI/CD tự động qua GitHub Actions.

**Nguồn dữ liệu:**

| Nguồn | Kiểu | Dùng cho | Độ khó chính |
|---|---|---|---|
| **SEC EDGAR Company Facts API** | Batch, REST | `feature/adf-copy-pipeline`, `feature/databricks-transform` | Schema drift, rate limit ~10 req/s, nested JSON |
| **Finnhub WebSocket** | Streaming, real-time | `feature/service-bus-eventing` | Long-lived connection, reconnect |

## Nguyên tắc & Quy ước Branching

- Model: `main` (snapshot ổn định) + `develop` (tích hợp) + feature branch theo từng US.
- Feature branch tách từ `develop`, merge về `develop` qua Pull Request.
- Branch merge xong thì xoá, giữ repo gọn.
- Merge `develop` → `main` theo milestone: sau P0 (tag `v0.1-mvp`), sau P1 (tag `v1.0`).
- Mỗi branch tối thiểu 2-3 commit có ý nghĩa.
- Format tên commit: `feat(adf): add copy activity blob to staging`, `fix(function): handle finnhub ws reconnect`, `docs(readme): add architecture diagram`.

## Bảng tổng quan Feature Branches

| # | Branch | User Story | Priority | Ước tính |
|---|---|---|---|---|
| 1 | `chore/project-init` | Khởi tạo | P0 | 0.5 ngày |
| 2 | `feature/data-source-adapter` | US-01 (SEC EDGAR) | P0 | 1.0 ngày |
| 3 | `feature/blob-ingest-service` | US-02 | P0 | 0.5 ngày |
| 4 | `feature/adf-copy-pipeline` | US-03, US-04 | P0 | 1.5 ngày |
| 5 | `feature/databricks-transform` | US-05, US-06 | P1 | 1.5 ngày |
| 6 | `feature/service-bus-eventing` | US-07, US-08 | P1 | 1.5 ngày |
| 7 | `feature/fastapi-expose-api` | US-09, US-10 | P0 | 1.0 ngày |
| 8 | `feature/apim-gateway` | US-11 | P1 | 0.5 ngày |
| 9 | `feature/cicd-github-actions` | US-12 | P0 | 1.0 ngày |
| 10 | `feature/docs-architecture` | US-13 | P0 | 1.0 ngày |

## Quy tắc nghiệp vụ (Business Rules)

| Rule | Mô tả |
|---|---|
| BR-01 | Không hardcode secret — dùng environment variable/GitHub Secrets; `.env` git-ignored |
| BR-02 | Mỗi pipeline stage phải idempotent — chạy lại nhiều lần không tạo duplicate |
| BR-03 | Databricks cluster phải cấu hình auto-termination (15-30 phút idle) |
| BR-04 | Mọi resource Azure gắn tag `project=azure-data-integration` |
| BR-05 | ADF Copy Activity và Azure Function phải có retry policy tối thiểu |
| BR-06 | API expose qua FastAPI không trả raw internal error ra ngoài |
| BR-07 | Mọi request tới SEC EDGAR bắt buộc có header `User-Agent` hợp lệ |

## Timeline

| Branch | Priority | Ước tính |
|---|---|---|
| `chore/project-init` | P0 | 0.5 ngày |
| `feature/data-source-adapter` | P0 | 1.0 ngày |
| `feature/blob-ingest-service` | P0 | 0.5 ngày |
| `feature/adf-copy-pipeline` | P0 | 1.5 ngày |
| `feature/fastapi-expose-api` | P0 | 1.0 ngày |
| `feature/cicd-github-actions` | P0 | 1.0 ngày |
| `feature/docs-architecture` | P0 | 1.0 ngày |
| **Subtotal MVP (P0)** | | **6.5 ngày** |
| `feature/databricks-transform` | P1 | 1.5 ngày |
| `feature/service-bus-eventing` | P1 | 1.5 ngày |
| `feature/apim-gateway` | P1 | 0.5 ngày |
| **Subtotal mở rộng (P1)** | | **3.5 ngày** |
| **Tổng toàn bộ plan** | | **10.0 ngày** |

## Checklist hoàn thành dự án

- [x] Toàn bộ branch đã merge vào `develop` qua Pull Request
- [x] Pipeline ADF chạy thành công end-to-end (mocked trong test, deploy infra thật qua Bicep)
- [x] Finnhub WS publisher chạy ổn định, Service Bus + Function xử lý tick đúng
- [x] FastAPI endpoint `/revenue/yearly` và `/market/live` trả JSON
- [x] APIM gateway có policy rate limit
- [x] CI/CD GitHub Actions: lint + test trên PR, deploy trên merge main
- [x] README có kiến trúc diagram + hướng dẫn chạy local + hướng dẫn deploy
- [x] Business rules BR-01..BR-07 tuân thủ trong toàn bộ codebase

## Lưu ý & Rủi ro khi triển khai

- **Databricks dễ đội chi phí** nếu quên tắt cluster — set auto-termination 15-30 phút (BR-03).
- **Container App chạy Finnhub WebSocket 24/7 là nguồn phí ẩn** dễ bị quên nhất. Bật khi demo, tắt ngay sau đó.
- **SEC EDGAR từ chối request nếu thiếu `User-Agent` hợp lệ** (lỗi 403 phổ biến nhất).
- **Setup Azure lần đầu tốn thời gian ở phần cấu hình quyền** (RBAC, connection string, managed identity) hơn là logic thật.
- **Không over-engineer**: 2 nguồn (SEC EDGAR + Finnhub) đủ để thể hiện batch + streaming.
- **README/diagram là bắt buộc** — nhà tuyển dụng đọc README hơn là tự deploy thử lại.