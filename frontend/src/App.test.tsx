// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (typeof window.localStorage?.clear === "function") {
    window.localStorage.clear();
  }
});

describe("App experiment builder", () => {
  it("renders the full-factorial run preview from selected dimensions", async () => {
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));

    expect((screen.getByLabelText("Design type") as HTMLSelectElement).value).toBe(
      "full_factorial",
    );
    expect(screen.getByRole("heading", { name: "Run matrix" })).toBeTruthy();
    expect(screen.getByText("Logical runs")).toBeTruthy();
    expect(screen.getAllByText("16").length).toBeGreaterThan(0);
    expect(screen.getByText("Run attempts")).toBeTruthy();
    expect(screen.getAllByText("32").length).toBeGreaterThan(0);
  });

  it("shows inline validation when a required dimension is missing", async () => {
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Chile Copper Memo/i }));

    expect(screen.getByRole("alert").textContent).toContain("Select at least one case.");
  });

  it("updates run preview from edited manifest JSON", async () => {
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    fireEvent.change(screen.getByLabelText("Manifest JSON editor"), {
      target: {
        value: JSON.stringify({
          id: "edited_manifest",
          name: "Edited manifest",
          cases: [{ id: "chile_copper_memo" }],
          models: [{ id: "openai_gpt_high" }],
          system_prompts: [{ id: "expert_investment_analyst_v3" }],
          warmers: [{ id: "none" }],
          design: {
            type: "full_factorial",
            replicates: 3,
            randomize_run_order: true,
          },
          evaluation: {
            blind_review: true,
            human_pairwise: true,
            evaluators: [{ id: "investment_memo_required_sections_v1" }],
          },
          controls: {
            max_parallel_requests: 1,
            max_total_cost_usd: 10,
            retry_failed: true,
            cache_provider_calls: true,
            local_only: true,
          },
        }),
      },
    });

    const preview = within(screen.getByRole("region", { name: "Run preview" }));
    expect(preview.getByText("1")).toBeTruthy();
    expect(preview.getByText("3")).toBeTruthy();
  });

  it("persists library records and draft experiments through the API before monitor display", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        if (String(url).endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
            preview: {
              logical_runs: 16,
              run_attempts: 32,
              estimated_token_count: 57600,
              estimated_cost_usd: 0.35,
            },
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    expect(requests.some((request) => request.url.endsWith("/library/warmers"))).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));

    expect(screen.getByText("Copper memo context sensitivity")).toBeTruthy();
    expect(screen.getByText("draft")).toBeTruthy();
    expect(screen.getAllByText("16").length).toBeGreaterThan(0);
    expect(screen.getAllByText("32").length).toBeGreaterThan(0);
  });

  it("surfaces manifest-save library conflicts before creating a draft", async () => {
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        const requestUrl = String(url);
        requests.push(requestUrl);
        if (requestUrl.endsWith("/library/cases")) {
          return jsonResponse({ detail: "Resource already exists." }, 409);
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Resource already exists.",
    );
    expect(requests.some((request) => request.endsWith("/experiments/drafts"))).toBe(false);
  });

  it("surfaces library conflicts without duplicating local records", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        if (String(url).endsWith("/library/cases")) {
          return jsonResponse({ detail: "Resource already exists." }, 409);
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Duplicate case");
    await userEvent.clear(screen.getByLabelText("Slug"));
    await userEvent.type(screen.getByLabelText("Slug"), "chile_copper_memo");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Resource already exists.");
    expect(screen.queryByText("Duplicate case")).toBeNull();
  });

  it("starts artifact preprocessing and shows sanitized derived output references", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        return jsonResponse({
          id: 7,
          status: "completed",
          parser_name: "pdf_text",
          derived_artifacts: [
            {
              id: 9,
              slug: "copper_supply_notes_pdf_text_7",
              name: "Copper Supply Notes extracted text",
              input_mode: "pdf_text",
              artifact_type: "text",
              checksum_sha256: "a".repeat(64),
              metadata: { parser_name: "pdf_text", page_count: 2 },
            },
          ],
        });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));
    fireEvent.change(screen.getByLabelText("Preprocessing parser"), {
      target: { value: "selected_figure" },
    });
    fireEvent.change(screen.getByLabelText("Parser payload JSON"), {
      target: {
        value: JSON.stringify({
          page_number: 2,
          region: { x: 4, y: 8, width: 16, height: 32 },
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Start preprocessing" }));

    await waitFor(() => {
      expect(
        requests.some((request) =>
          request.url.endsWith(
            "/projects/default/library/artifacts/copper_supply_notes/preprocessing-runs",
          ),
        ),
      ).toBe(true);
    });
    expect(requests[requests.length - 1]?.body).toContain('"parser_name":"selected_figure"');
    expect(requests[requests.length - 1]?.body).toContain('"page_number":2');
    expect(requests[requests.length - 1]?.body).toContain('"width":16');
    expect(screen.getByText("Local storage only")).toBeTruthy();
    expect(screen.getAllByText("copper_supply_notes_pdf_text_7").length).toBeGreaterThan(0);
    expect(screen.queryByText(/local:\/\/examples/)).toBeNull();
  });

  it("keeps prior derived outputs visible when the latest preprocessing run fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/derived-artifacts")) {
          return jsonResponse([
            {
              id: 6,
              slug: "loaded_pdf_text",
              name: "Loaded PDF text",
              input_mode: "pdf_text",
              artifact_type: "text",
              metadata: { parser_name: "pdf_text", page_count: 1 },
            },
          ]);
        }
        if (path.endsWith("/preprocessing-runs") && init?.method === "POST") {
          return jsonResponse({
            id: 9,
            status: "failed",
            parser_name: "pdf_text",
            error_kind: "unreadable_pdf",
            error_message: "PDF could not be read.",
            derived_artifacts: [],
          });
        }
        if (path.endsWith("/preprocessing-runs")) {
          return jsonResponse([]);
        }
        return jsonResponse({ id: 1, slug: "ok" });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));
    await waitFor(() => {
      expect(screen.getAllByText("loaded_pdf_text").length).toBeGreaterThan(0);
    });
    await userEvent.click(screen.getByRole("button", { name: "Start preprocessing" }));

    expect((await screen.findByRole("alert")).textContent).toContain("PDF could not be read.");
    expect(screen.getAllByText("loaded_pdf_text").length).toBeGreaterThan(0);
  });

  it("clears prior derived outputs when preprocessing succeeds without outputs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/derived-artifacts")) {
          return jsonResponse([
            {
              id: 6,
              slug: "loaded_pdf_text",
              name: "Loaded PDF text",
              input_mode: "pdf_text",
              artifact_type: "text",
              metadata: { parser_name: "pdf_text", page_count: 1 },
            },
          ]);
        }
        if (path.endsWith("/preprocessing-runs") && init?.method === "POST") {
          return jsonResponse({
            id: 10,
            status: "succeeded",
            parser_name: "pdf_text",
            error_kind: null,
            error_message: null,
            derived_artifacts: [],
          });
        }
        if (path.endsWith("/preprocessing-runs")) {
          return jsonResponse([]);
        }
        return jsonResponse({ id: 1, slug: "ok" });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));
    await waitFor(() => {
      expect(screen.getAllByText("loaded_pdf_text").length).toBeGreaterThan(0);
    });
    await userEvent.click(screen.getByRole("button", { name: "Start preprocessing" }));

    await screen.findByText("No derived outputs available.");
    expect(screen.queryByLabelText("Derived artifacts")).toBeNull();
  });

  it("updates an existing artifact input mode through the artifact patch API", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ method: init?.method ?? "GET", url: String(url), body: init?.body?.toString() });
        return jsonResponse({ id: 1, slug: "copper_supply_notes", input_mode: "pdf_text" });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));
    fireEvent.change(screen.getByLabelText("Input mode"), {
      target: { value: "pdf_text" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "PATCH" &&
            request.url.endsWith("/library/artifacts/copper_supply_notes/input-mode"),
        ),
      ).toBe(true);
    });
    const patch = requests.find((request) => request.method === "PATCH");
    expect(patch?.body).toContain('"input_mode":"pdf_text"');
  });

  it("rejects unsupported edits to existing artifacts instead of discarding them", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ method: init?.method ?? "GET", url: String(url), body: init?.body?.toString() });
        if (String(url).endsWith("/preprocessing-runs") || String(url).endsWith("/derived-artifacts")) {
          return jsonResponse([]);
        }
        return jsonResponse({ id: 1, slug: "ok" });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Edited artifact");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Existing artifacts only support input mode updates.",
    );
    expect(
      requests.some(
        (request) =>
          request.method === "PATCH" &&
          request.url.endsWith("/library/artifacts/copper_supply_notes/input-mode"),
      ),
    ).toBe(false);
  });

  it("loads preprocessing history and derived artifacts when inspecting an artifact", async () => {
    const requests: Array<{ method?: string; url: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        const request = { method: init?.method ?? "GET", url: String(url) };
        requests.push(request);
        if (request.url.endsWith("/preprocessing-runs")) {
          return jsonResponse([
            {
              id: 5,
              source_artifact_id: 1,
              parser_name: "pdf_text",
              parser_version: "1.0.0",
              status: "completed",
              derived_artifact_ids: [6],
              derived_artifacts: [],
            },
          ]);
        }
        if (request.url.endsWith("/derived-artifacts")) {
          return jsonResponse([
            {
              id: 6,
              slug: "loaded_pdf_text",
              name: "Loaded PDF text",
              input_mode: "pdf_text",
              artifact_type: "text",
              metadata: { parser_name: "pdf_text", page_count: 1 },
              local_storage: { available: true, reference: "local_artifact_storage" },
            },
          ]);
        }
        return jsonResponse({ id: 1, slug: "ok" });
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(screen.getByRole("button", { name: /Copper Supply Notes/i }));

    await waitFor(() => {
      expect(screen.getAllByText("loaded_pdf_text").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("pdf_text: completed")).toBeTruthy();
    expect(
      requests.some(
        (request) => request.method === "GET" && request.url.endsWith("/derived-artifacts"),
      ),
    ).toBe(true);
  });

  it("lets the experiment builder select artifact input modes in the manifest", async () => {
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Copper Supply Notes/i }));
    fireEvent.change(screen.getByLabelText("Copper Supply Notes input mode"), {
      target: { value: "pdf_text" },
    });

    await waitFor(() => {
      expect((screen.getByLabelText("Manifest JSON editor") as HTMLTextAreaElement).value).toContain(
        '"input_mode": "pdf_text"',
      );
    });
  });

  it("validates LLM judge editor model references and output schema before saving", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        return jsonResponse({ id: 1, slug: "memo_quality_judge" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Evaluators" }));
    fireEvent.change(screen.getByLabelText("Definition JSON"), {
      target: { value: "{not valid json" },
    });
    await userEvent.click(screen.getByRole("tab", { name: "LLM judges" }));
    await userEvent.type(screen.getByLabelText("Name"), "Memo Quality Judge");
    await userEvent.type(screen.getByLabelText("Slug"), "memo_quality_judge");
    await userEvent.type(screen.getByLabelText("Judge prompt"), "Score the answer.");

    await userEvent.clear(screen.getByLabelText("Judge model config"));
    await userEvent.type(screen.getByLabelText("Judge model config"), "missing_model");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Judge model config must reference an existing model config.",
    );
    expect(requests).toHaveLength(0);

    await userEvent.clear(screen.getByLabelText("Judge model config"));
    await userEvent.type(screen.getByLabelText("Judge model config"), "openai_gpt_high");
    fireEvent.change(screen.getByLabelText("Output schema JSON"), {
      target: { value: JSON.stringify({ type: "array" }) },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Output schema must be a JSON object schema.",
    );
    expect(requests).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Output schema JSON"), {
      target: {
        value: JSON.stringify({
          type: "object",
          properties: { score: { type: "number" } },
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(
        requests.some((request) => request.url.endsWith("/library/llm-judge-configs")),
      ).toBe(true);
    });
    expect(requests[0].body).toContain('"judge_model_config_slug":"openai_gpt_high"');
  });

  it("saves metric adapter configs from the library editor", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        return jsonResponse({ id: 1, slug: "retrieval_precision_local" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Metric adapters" }));
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Retrieval Precision Local");
    await userEvent.clear(screen.getByLabelText("Slug"));
    await userEvent.type(screen.getByLabelText("Slug"), "retrieval_precision_local");
    fireEvent.change(screen.getByLabelText("Required inputs"), {
      target: { value: "answer_text\nretrieved_chunks" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(
        requests.some((request) => request.url.endsWith("/library/metric-adapter-configs")),
      ).toBe(true);
    });
    expect(requests[0].body).toContain('"adapter_kind":"retrieval_precision"');
    expect(requests[0].body).toContain('"required_inputs":["answer_text","retrieved_chunks"]');
  });

  it("saves benchmark suites and applies split filters to generated manifests", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        return jsonResponse({ id: 1, slug: "copper_suite" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Benchmark suites" }));
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Copper validation suite");
    await userEvent.clear(screen.getByLabelText("Slug"));
    await userEvent.type(screen.getByLabelText("Slug"), "copper_validation_suite");
    fireEvent.change(screen.getByLabelText("Case IDs"), {
      target: { value: "chile_copper_memo" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(
        requests.some((request) => request.url.endsWith("/library/benchmark-suites")),
      ).toBe(true);
    });
    expect(requests[0].body).toContain('"case_ids":["chile_copper_memo"]');

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    fireEvent.change(screen.getByLabelText("Benchmark suite"), {
      target: { value: "copper_validation_suite" },
    });
    fireEvent.change(screen.getByLabelText("Suite split"), {
      target: { value: "validation" },
    });

    expect((screen.getByLabelText("Manifest JSON editor") as HTMLTextAreaElement).value).toContain(
      '"split": "validation"',
    );
  });

  it("queues an already-saved draft through the persisted experiment id", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({ url: String(url), body: init?.body?.toString() });
        if (String(url).endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
            preview: {
              logical_runs: 16,
              run_attempts: 32,
              estimated_token_count: 57600,
              estimated_cost_usd: 0.35,
            },
          });
        }
        if (String(url).endsWith("/experiments/42/queue")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "queued",
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Queue run" }));

    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/42/queue"))).toBe(true);
    });
    expect(
      requests.filter((request) => request.url.endsWith("/experiments/drafts")).length,
    ).toBe(1);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    expect(screen.getByText("queued")).toBeTruthy();
    expect(screen.getByText("Remaining queued").parentElement?.textContent).toContain("16");
  });

  it("refreshes an edited saved draft before queueing it", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({
          method: init?.method,
          url: String(url),
          body: init?.body?.toString(),
        });
        if (String(url).endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        if (String(url).endsWith("/experiments/42/draft")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        if (String(url).endsWith("/experiments/42/queue")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "queued",
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    fireEvent.change(screen.getByLabelText("Manifest JSON editor"), {
      target: {
        value: JSON.stringify({
          id: "copper_memo_context_sensitivity",
          name: "Copper memo context sensitivity",
          cases: [{ id: "chile_copper_memo" }],
          models: [{ id: "openai_gpt_high" }],
          system_prompts: [{ id: "expert_investment_analyst_v3" }],
          warmers: [{ id: "none" }],
          design: {
            type: "full_factorial",
            replicates: 3,
            randomize_run_order: true,
          },
          evaluation: {
            blind_review: true,
            human_pairwise: true,
            evaluators: [{ id: "investment_memo_required_sections_v1" }],
          },
          controls: {
            max_parallel_requests: 1,
            max_total_cost_usd: 10,
            retry_failed: true,
            cache_provider_calls: true,
            local_only: true,
          },
        }),
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Queue run" }));

    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/42/queue"))).toBe(true);
    });
    const updateRequest = requests.find((request) => request.url.endsWith("/experiments/42/draft"));
    expect(updateRequest?.method).toBe("PUT");
    expect(updateRequest?.body).toContain('"replicates":3');
  });

  it("refreshes a saved draft after builder control edits before queueing it", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({
          method: init?.method,
          url: String(url),
          body: init?.body?.toString(),
        });
        if (String(url).endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        if (String(url).endsWith("/experiments/42/draft")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        if (String(url).endsWith("/experiments/42/queue")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            name: "Copper memo context sensitivity",
            status: "queued",
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    fireEvent.change(screen.getByLabelText("Replicates"), {
      target: { value: "3" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Queue run" }));

    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/42/queue"))).toBe(true);
    });
    const updateRequest = requests.find((request) => request.url.endsWith("/experiments/42/draft"));
    expect(updateRequest?.method).toBe("PUT");
    expect(updateRequest?.body).toContain('"replicates":3');
  });

  it("reuses an id-less saved draft even when the server slug differs", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        requests.push({
          method: init?.method,
          url: String(url),
          body: init?.body?.toString(),
        });
        if (String(url).endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "Copper memo context sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        if (String(url).endsWith("/experiments/42/draft")) {
          return jsonResponse({
            id: 42,
            slug: "Copper memo context sensitivity",
            name: "Copper memo context sensitivity",
            status: "draft",
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    const manifest = JSON.parse(
      (screen.getByLabelText("Manifest JSON editor") as HTMLTextAreaElement).value,
    ) as Record<string, unknown>;
    delete manifest.id;
    fireEvent.change(screen.getByLabelText("Manifest JSON editor"), {
      target: { value: JSON.stringify(manifest) },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.filter((request) => request.url.endsWith("/experiments/drafts"))).toHaveLength(
        1,
      );
    });
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/42/draft"))).toBe(true);
    });
    expect(requests.filter((request) => request.url.endsWith("/experiments/drafts"))).toHaveLength(
      1,
    );
  });
});

describe("App run monitor", () => {
  it("renders run states, progress metrics, safeguards, and filters failures", async () => {
    vi.stubGlobal("fetch", vi.fn(monitorFetch([], { emptyAttemptRuns: [1] })));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));

    const monitor = await screen.findByRole("region", { name: "Run Monitor table" });
    expect(within(monitor).getByText("pending")).toBeTruthy();
    expect(within(monitor).getByText("running")).toBeTruthy();
    expect(within(monitor).getByText("failed")).toBeTruthy();
    expect(within(monitor).getByText("complete")).toBeTruthy();
    expect(within(monitor).getByText("canceled")).toBeTruthy();
    expect(within(monitor).getByText("skipped")).toBeTruthy();
    expect(screen.getByText("Total runs")).toBeTruthy();
    expect(screen.getByText("Completed runs")).toBeTruthy();
    expect(screen.getByText("Failed runs")).toBeTruthy();
    expect(screen.getByText("Remaining queued")).toBeTruthy();
    expect(screen.getByText("Total runs").parentElement?.textContent).toContain("6");
    expect(screen.getByText("Completed runs").parentElement?.textContent).toContain("1");
    expect(screen.getByText("Failed runs").parentElement?.textContent).toContain("1");
    expect(screen.getByText("Total attempts").parentElement?.textContent).toContain("5");
    expect(screen.getByText("Total cost").parentElement?.textContent).toContain("$0.73");
    expect(screen.getByText("Average latency").parentElement?.textContent).toContain("1.1s");
    expect(screen.getByText("Remaining queued").parentElement?.textContent).toContain("1");
    expect(screen.getByText("Cost cap exceeded before provider call.")).toBeTruthy();
    expect(screen.getByText("Provider is blocked by allow/deny configuration.")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });

    expect(within(monitor).getByText("case-cost-cap")).toBeTruthy();
    expect(within(monitor).queryByText("case-running")).toBeNull();
  });

  it("keeps monitor rows visible when one attempt fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(monitorFetch([], { failedAttemptRuns: [2] })));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));

    const monitor = await screen.findByRole("region", { name: "Run Monitor table" });
    expect(within(monitor).getByText("case-running")).toBeTruthy();
    expect(within(monitor).getByText("case-cost-cap")).toBeTruthy();
    expect(within(monitor).queryByText("browser-session")).toBeNull();
  });

  it("retries a failed run through the monitor API", async () => {
    const requests: Array<{ method?: string; url: string }> = [];
    vi.stubGlobal("fetch", vi.fn(monitorFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Retry failed run case-cost-cap" }),
    );

    await waitFor(() => {
      expect(
        requests.some(
          (request) => request.method === "POST" && request.url.endsWith("/monitor/runs/3/retry"),
        ),
      ).toBe(true);
    });
  });

  it("cancels an experiment through the monitor API", async () => {
    const requests: Array<{ method?: string; url: string }> = [];
    vi.stubGlobal("fetch", vi.fn(monitorFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Cancel experiment Copper monitor" }),
    );

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" && request.url.endsWith("/monitor/experiments/42/cancel"),
        ),
      ).toBe(true);
    });
  });

  it("opens an API experiment from the monitor in the review workspace", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(monitorFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Use Copper monitor for review and results",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" &&
            request.url.endsWith("/projects/copper-memo-demo/experiments/42/review-sets"),
        ),
      ).toBe(true);
    });
    const createRequest = requests.find(
      (request) =>
        request.method === "POST" &&
        request.url.endsWith("/projects/copper-memo-demo/experiments/42/review-sets"),
    );
    expect(createRequest?.body).toContain('"slug":"copper-monitor-human-review"');
    expect(screen.getByText("blind output A")).toBeTruthy();
  });

  it("disables review use for incomplete API experiments", async () => {
    vi.stubGlobal("fetch", vi.fn(monitorFetch([], { experimentStatus: "running" })));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));

    const useButton = await screen.findByRole("button", {
      name: "Use Copper monitor for review and results",
    });
    expect((useButton as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens attempt details with metadata, timing, token, cost, and error fields", async () => {
    vi.stubGlobal("fetch", vi.fn(monitorFetch()));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Inspect run case-cost-cap" }),
    );

    const drawer = await screen.findByRole("dialog", { name: "Attempt details" });
    expect(within(drawer).getByText("Request metadata")).toBeTruthy();
    expect(within(drawer).getByText(/"model": "gpt-5.5"/)).toBeTruthy();
    expect(within(drawer).getByText("Response metadata")).toBeTruthy();
    expect(within(drawer).getByText(/"blocked": true/)).toBeTruthy();
    expect(within(drawer).getByText("Latency")).toBeTruthy();
    expect(within(drawer).getByText("1.2s")).toBeTruthy();
    expect(within(drawer).getByText("Tokens")).toBeTruthy();
    expect(within(drawer).getByText("1,200")).toBeTruthy();
    expect(within(drawer).getByText("Cost")).toBeTruthy();
    expect(within(drawer).getByText("$0.42")).toBeTruthy();
    expect(within(drawer).getByText("blocked_by_config")).toBeTruthy();
    expect(within(drawer).getByText("Cost cap exceeded before provider call.")).toBeTruthy();
    expect(within(drawer).getByText("cost_cap_exceeded")).toBeTruthy();
  });
});

describe("App comparison workspace", () => {
  it("creates a blind review set and submits pairwise human scores", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();
    expect(screen.getByText("blind output B")).toBeTruthy();
    expect(screen.queryByText("model_a")).toBeNull();
    expect(screen.queryByText("system")).toBeNull();
    const createRequest = requests.find((request) =>
      request.url.endsWith("/experiments/42/review-sets"),
    );
    expect(createRequest?.body).not.toContain("random_seed");

    await userEvent.click(screen.getByRole("button", { name: "Prefer A" }));
    await userEvent.click(screen.getByRole("button", { name: "Pass A" }));
    await userEvent.click(screen.getByRole("button", { name: "Fail B" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Answer B: too generic" }));
    await userEvent.type(screen.getByLabelText("Review notes"), "A is more decision-useful.");
    await userEvent.click(screen.getByRole("button", { name: "Submit review" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" && request.url.endsWith("/review-assignments/170/decision"),
        ),
      ).toBe(true);
    });
    const decision = requests.find((request) =>
      request.url.endsWith("/review-assignments/170/decision"),
    );
    const decisionBody = JSON.parse(decision?.body ?? "{}") as { reviewer_id: string };
    expect(decisionBody.reviewer_id).toMatch(/^human-/);
    expect(decisionBody.reviewer_id).not.toBe("human");
    expect(decision?.body).toContain('"winner":"A"');
    expect(decision?.body).toContain('"A":true');
    expect(decision?.body).toContain('"B":false');
    expect(decision?.body).toContain('"too generic"');
    expect(decision?.body).toContain("A is more decision-useful.");
  });

  it("creates assignments when switching reviewer queues", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Reviewer"), { target: { value: "Alice Reviewer" } });
    await userEvent.click(screen.getByRole("button", { name: "Load queue" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" && request.url.endsWith("/review-sets/7/assignments"),
        ),
      ).toBe(true);
    });
    const assignmentRequest = requests.find((request) =>
      request.url.endsWith("/review-sets/7/assignments"),
    );
    expect(assignmentRequest?.body).toContain('"alice_reviewer"');
  });

  it("loads an existing review set after a slug conflict", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests, { createConflict: true })));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();
    expect(
      requests.some((request) =>
        request.url.endsWith(
          "/experiments/42/review-sets?slug=copper_memo_context_sensitivity-human-review",
        ),
      ),
    ).toBe(true);
  });

  it("keeps comparison workspace usable when localStorage rejects reviewer ids", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));

    expect(screen.getByRole("button", { name: "Create blind review set" })).toBeTruthy();
    expect(getItem).toHaveBeenCalled();
  });

  it("keeps failure tags scoped to failed answers", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Fail A" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Answer A: weak risks" }));
    await userEvent.click(screen.getByRole("button", { name: "Fail B" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Answer B: too generic" }));
    await userEvent.click(screen.getByRole("button", { name: "Pass A" }));
    await userEvent.click(screen.getByRole("button", { name: "Submit review" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" && request.url.endsWith("/review-assignments/170/decision"),
        ),
      ).toBe(true);
    });
    const decision = requests.find((request) =>
      request.url.endsWith("/review-assignments/170/decision"),
    );
    const body = JSON.parse(decision?.body ?? "{}") as { failure_tags: Record<string, string[]> };
    expect(body.failure_tags).toEqual({ B: ["too generic"] });
  });

  it("keeps model metadata hidden until reveal", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();
    expect(screen.queryByText("model_a")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Reveal metadata" }));

    expect(await screen.findByText("model_a")).toBeTruthy();
    expect(screen.getByText("model_b")).toBeTruthy();
    expect(screen.getAllByText("system")).toHaveLength(2);
    expect(screen.getAllByText("warmer")).toHaveLength(2);
    expect(screen.getByText("$0.12")).toBeTruthy();
    expect(screen.getByText("$0.18")).toBeTruthy();
    expect(
      requests.some((request) => request.url.endsWith("/review-sets/7?reveal_metadata=true")),
    ).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Submit review" }));
    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" && request.url.endsWith("/review-assignments/170/decision"),
        ),
      ).toBe(true);
    });
  });

  it("navigates between blind review pairs", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(reviewFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("blind output A")).toBeTruthy();
    expect(screen.getByText("Pair 1 of 2")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Next pair" }));

    expect(screen.getByText("second blind output A")).toBeTruthy();
    expect(screen.getByText("second blind output B")).toBeTruthy();
    expect(screen.getByText("Pair 2 of 2")).toBeTruthy();
    expect(screen.queryByText("model_c")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Previous pair" }));

    expect(screen.getByText("blind output A")).toBeTruthy();
    expect(screen.getByText("Pair 1 of 2")).toBeTruthy();
  });
});

describe("App results analytics", () => {
  it("loads analytics and renders scores, costs, lift, failure tags, and caution copy", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    const { container } = render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    expect(await screen.findByText("Score table")).toBeTruthy();
    expect(screen.getByText(/Numeric result rates are uncalibrated directional summaries/)).toBeTruthy();
    expect(screen.getByText("Pass rate").parentElement?.textContent).toContain("67%");
    expect(screen.getByText("Win rate").parentElement?.textContent).toContain("50%");
    expect(screen.getAllByText("Failure rate")[0].parentElement?.textContent).toContain("25%");
    expect(screen.getByText("Total tokens").parentElement?.textContent).toContain("485");
    expect(screen.getAllByText("too generic").length).toBeGreaterThan(0);
    expect(screen.getByText("Warmer lift chart")).toBeTruthy();
    expect(screen.getAllByText("+25%").length).toBeGreaterThan(0);
    expect(container.querySelector(".result-callout")?.textContent).toBe("+25%");
    expect(screen.getByText("Context sensitivity table")).toBeTruthy();
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
    expect(container.querySelector(".sensitivity-label")?.textContent).toBe("High");
    expect(screen.getByText("Replicate reliability")).toBeTruthy();
    expect(screen.getByText("Low Sample")).toBeTruthy();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.getByText("Cost-quality frontier")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Frontier" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Uncertainty" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Calibration" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sensitivity" })).toBeTruthy();
    expect(screen.getByLabelText("Model filter")).toBeTruthy();
    expect(screen.getAllByText("Dominated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Frontier").length).toBeGreaterThan(1);
    expect(screen.getAllByText(/Single Sample/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/judge_1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/100%/).length).toBeGreaterThan(0);
    expect(screen.getByText("Failure rate by model, prompt, warmer, and case")).toBeTruthy();
    expect(screen.getByText("Reviewer coverage")).toBeTruthy();
    expect(screen.getByText("Reviewer disagreement")).toBeTruthy();
    expect(screen.getByText("Failure taxonomy rollup")).toBeTruthy();
    expect(screen.getByText("Divergence metrics")).toBeTruthy();
    expect(screen.getAllByText("Deterministic heuristic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Judge backed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Human backed").length).toBeGreaterThan(0);
    expect(screen.getByText("Carryover audit")).toBeTruthy();
    expect(screen.getByText("Reused")).toBeTruthy();
    expect(screen.getAllByText("Heuristic").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Markdown" })).toBeTruthy();
    expect(requests.some((request) => request.url.endsWith("/monitor/experiments/42/analytics"))).toBe(
      true,
    );
  });

  it("applies Results frontier filters through the analytics API", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Cost-quality frontier");
    await userEvent.click(screen.getByRole("button", { name: "Frontier" }));
    await userEvent.selectOptions(screen.getByLabelText("Model filter"), "model_a");
    await userEvent.selectOptions(screen.getByLabelText("Warmer filter"), "analyst");

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.url.includes("/monitor/experiments/42/analytics?") &&
            request.url.includes("model_config_slug=model_a") &&
            request.url.includes("warmer_slug=analyst"),
        ),
      ).toBe(true);
    });

    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.click(screen.getByRole("button", { name: "Promptfoo" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.url.includes("/monitor/experiments/42/exports?") &&
            request.url.includes("format=promptfoo") &&
            request.url.includes("model_config_slug=model_a") &&
            request.url.includes("warmer_slug=analyst"),
        ),
      ).toBe(true);
    });
  });

  it("runs metric adapter scoring from the results view", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Metric adapters");
    expect((screen.getByLabelText("Metric adapter") as HTMLSelectElement).value).toBe(
      "retrieval_precision_local@1",
    );
    await userEvent.click(screen.getByRole("button", { name: "Run adapter" }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.method === "POST" &&
            request.url.endsWith("/monitor/experiments/42/metric-adapters/run"),
        ),
      ).toBe(true);
    });
    const request = requests.find((item) =>
      item.url.endsWith("/monitor/experiments/42/metric-adapters/run"),
    );
    expect(request?.body).toContain('"adapter_config_slug":"retrieval_precision_local"');
    expect(request?.body).toContain('"adapter_config_version":1');
    expect(await screen.findByText("retrieval_precision")).toBeTruthy();
    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByText("Strong")).toBeTruthy();
    expect(
      requests.some(
        (item) =>
          item.method === "POST" && item.url.endsWith("/library/metric-adapter-configs"),
      ),
    ).toBe(true);
  });

  it("exports promptfoo from results and renders export warnings", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Export actions");
    await userEvent.click(screen.getByRole("button", { name: "Promptfoo" }));

    await screen.findByText("Promptfoo export warning");
    expect(screen.getByText(/unsupported evaluator/i)).toBeTruthy();
    expect((screen.getByLabelText("Export content") as HTMLTextAreaElement).value).toContain(
      "description: Promptfoo export",
    );
    expect(
      requests.some((request) =>
        request.url.endsWith("/monitor/experiments/42/exports?format=promptfoo"),
      ),
    ).toBe(true);
  });

  it("exports OpenTelemetry JSON from results with metadata-only local trace labeling", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Export actions");
    await screen.findByText(/Metadata-only local-file trace/i);
    await userEvent.click(screen.getByRole("button", { name: "OpenTelemetry JSON" }));

    expect((await screen.findByLabelText("Export content") as HTMLTextAreaElement).value).toContain(
      '"format_version": "model_eval_otel_trace_v1"',
    );
    expect(
      requests.some((request) =>
        request.url.endsWith("/monitor/experiments/42/exports?format=otel-json"),
      ),
    ).toBe(true);
  });

  it("previews promptfoo import content and renders import warnings", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Promptfoo import");
    fireEvent.change(screen.getByLabelText("Promptfoo config"), {
      target: { value: "description: Smoke\nprompts:\n  - Hello {{topic}}" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Preview import" }));

    await screen.findByText("Promptfoo import warning");
    expect(screen.getByText(/ambiguous prompt shape/i)).toBeTruthy();
    const request = requests.find((item) =>
      item.url.endsWith("/projects/default/imports/promptfoo/preview"),
    );
    expect(request?.method).toBe("POST");
    expect(request?.body).toContain("description: Smoke");
  });

  it("persists promptfoo import content from results", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(resultsFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Promptfoo import");
    fireEvent.change(screen.getByLabelText("Promptfoo config"), {
      target: { value: "description: Smoke\nprompts:\n  - Hello {{topic}}" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Persist import" }));

    await screen.findByText(/Promptfoo import persisted/i);
    const request = requests.find(
      (item) =>
        item.url.endsWith("/projects/default/imports/promptfoo/preview") &&
        item.body?.includes('"persist":true'),
    );
    expect(request?.method).toBe("POST");
  });

  it("persists promptfoo import content to the selected experiment project", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url);
        requests.push({ method: init?.method ?? "GET", url: path, body: init?.body?.toString() });
        if (path.endsWith("/experiments/drafts")) {
          return jsonResponse({
            id: 42,
            slug: "copper_memo_context_sensitivity",
            project_slug: "research",
            name: "Copper memo context sensitivity",
            status: "complete",
          });
        }
        if (path.endsWith("/monitor/experiments/42/analytics")) {
          return jsonResponse(resultsAnalyticsFixture());
        }
        if (path.endsWith("/projects/research/imports/promptfoo/preview")) {
          return jsonResponse({
            manifest: { name: "Promptfoo import" },
            preview: { logical_runs: 1, run_attempts: 1 },
            warnings: [],
            library_records: { metric_adapter_configs: [] },
            persisted: {
              project_slug: "research",
              created: {
                cases: 1,
                system_prompts: 1,
                warmers: 1,
                model_configs: 1,
                evaluators: 1,
                metric_adapter_configs: 0,
              },
            },
          });
        }
        return jsonResponse({ id: 1, slug: "ok" }, 201);
      }),
    );

    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: "Experiment Builder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith("/experiments/drafts"))).toBe(true);
    });
    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    await screen.findByText("Promptfoo import");
    fireEvent.change(screen.getByLabelText("Promptfoo config"), {
      target: { value: "description: Smoke\nprompts:\n  - Hello {{topic}}" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Persist import" }));

    await screen.findByText(/Promptfoo import persisted/i);
    expect(
      requests.some(
        (request) =>
          request.url.endsWith("/projects/research/imports/promptfoo/preview") &&
          request.body?.includes('"persist":true'),
      ),
    ).toBe(true);
  });
});

describe("V2 demo frontend smoke", () => {
  it("opens the V2 demo across Library, Run Monitor, Comparison Workspace, and Results", async () => {
    const requests: Array<{ method?: string; url: string; body?: string }> = [];
    vi.stubGlobal("fetch", vi.fn(v2DemoFetch(requests)));

    render(<App />);

    await userEvent.click(screen.getByRole("tab", { name: "Benchmark suites" }));
    expect(screen.getByText("V2 Copper Benchmark Suite")).toBeTruthy();
    expect(screen.getByText("v2_copper_benchmark_suite")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Run Monitor" }));
    expect(
      (await screen.findAllByText("V2 Copper Benchmark Suite all suite run")).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Total runs").parentElement?.textContent).toContain("16");
    expect(screen.getByText("Total attempts").parentElement?.textContent).toContain("32");

    await userEvent.click(
      screen.getByRole("button", {
        name: "Use V2 Copper Benchmark Suite all suite run for review and results",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Comparison Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Create blind review set" }));

    expect(await screen.findByText("V2 local answer A")).toBeTruthy();
    expect(screen.getByText("V2 local answer B")).toBeTruthy();
    expect(screen.getByText("V2 copper demo multi-reviewer calibration")).toBeTruthy();
    expect(screen.getByText("Assigned").parentElement?.textContent).toContain("32");

    await userEvent.click(screen.getByRole("button", { name: "Results" }));

    expect(await screen.findByText("Cost-quality frontier")).toBeTruthy();
    expect(screen.getByText("Divergence metrics")).toBeTruthy();
    expect(screen.getAllByText("v2_retrieval_precision v1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("v2_synthetic_judge 78%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dominated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Frontier").length).toBeGreaterThan(1);
    expect(screen.getAllByText("Judge backed").length).toBeGreaterThan(0);
    expect(
      requests.some((request) =>
        request.url.endsWith("/monitor/experiments/99/analytics"),
      ),
    ).toBe(true);
    expect(requests.every((request) => v2DemoRequestWasHandled(request.url))).toBe(true);
  });
});

function monitorFetch(
  requests: Array<{ method?: string; url: string; body?: string }> = [],
  options: {
    emptyAttemptRuns?: number[];
    experimentStatus?: string;
    failedAttemptRuns?: number[];
  } = {},
) {
  return async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    requests.push({ method: init?.method ?? "GET", url: path, body: init?.body?.toString() });
    if (path.endsWith("/monitor/experiments")) {
      return jsonResponse([
        {
          id: 42,
          project_slug: "copper-memo-demo",
          slug: "copper-monitor",
          name: "Copper monitor",
          status: options.experimentStatus ?? "complete",
        },
      ]);
    }
    if (path.endsWith("/monitor/experiments/42/runs")) {
      return jsonResponse([
        runFixture(1, "case-pending", "pending"),
        runFixture(2, "case-running", "running"),
        runFixture(3, "case-cost-cap", "failed"),
        runFixture(4, "case-complete", "complete"),
        runFixture(5, "case-canceled", "canceled"),
        runFixture(6, "case-provider-block", "skipped"),
      ]);
    }
    const attemptMatch = path.match(/\/monitor\/runs\/(\d+)\/attempts$/);
    if (attemptMatch) {
      const runId = Number(attemptMatch[1]);
      if (options.failedAttemptRuns?.includes(runId)) {
        return jsonResponse({ detail: "attempts unavailable" }, 503);
      }
      if (options.emptyAttemptRuns?.includes(runId)) {
        return jsonResponse([]);
      }
      return jsonResponse([attemptFixture(runId)]);
    }
    if (path.endsWith("/monitor/runs/3/retry")) {
      return jsonResponse({ ...attemptFixture(3), id: 99, status: "queued", attempt_number: 2 });
    }
    if (path.endsWith("/monitor/experiments/42/cancel")) {
      return jsonResponse({
        id: 42,
        slug: "copper-monitor",
        name: "Copper monitor",
        status: "canceled",
      });
    }
    if (path.endsWith("/projects/copper-memo-demo/reviewers")) {
      return jsonResponse(reviewerFixture(init), 201);
    }
    if (path.endsWith("/projects/copper-memo-demo/experiments/42/review-sets")) {
      return jsonResponse(reviewSetFixture(false), 201);
    }
    if (path.match(/\/review-sets\/7\/reviewers\/[^/]+\/queue$/)) {
      return jsonResponse(reviewQueueFixture(path));
    }
    return jsonResponse({ ok: true });
  };
}

function v2DemoFetch(requests: Array<{ method?: string; url: string; body?: string }>) {
  return async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    requests.push({ method: init?.method ?? "GET", url: path, body: init?.body?.toString() });
    if (path.endsWith("/monitor/experiments")) {
      return jsonResponse([
        {
          id: 99,
          project_slug: "v2-copper-demo",
          slug: "v2_copper_benchmark_suite_v1_all_suite_run",
          name: "V2 Copper Benchmark Suite all suite run",
          status: "complete",
        },
      ]);
    }
    if (path.endsWith("/monitor/experiments/99/runs")) {
      return jsonResponse(v2RunFixtures());
    }
    const attemptMatch = path.match(/\/monitor\/runs\/(\d+)\/attempts$/);
    if (attemptMatch) {
      return jsonResponse(v2AttemptFixtures(Number(attemptMatch[1])));
    }
    if (path.endsWith("/projects/v2-copper-demo/reviewers")) {
      return jsonResponse(reviewerFixture(init), 201);
    }
    if (path.endsWith("/projects/v2-copper-demo/experiments/99/review-sets")) {
      return jsonResponse(v2ReviewSetFixture(false), 201);
    }
    if (path.match(/\/review-sets\/88\/reviewers\/[^/]+\/queue$/)) {
      return jsonResponse(v2ReviewQueueFixture(path));
    }
    if (
      path.endsWith("/monitor/experiments/99/analytics") ||
      path.includes("/monitor/experiments/99/analytics?")
    ) {
      return jsonResponse(v2ResultsAnalyticsFixture());
    }
    throw new Error(`Unexpected V2 demo API request: ${init?.method ?? "GET"} ${path}`);
  };
}

function v2DemoRequestWasHandled(path: string): boolean {
  return (
    path.endsWith("/monitor/experiments") ||
    path.endsWith("/monitor/experiments/99/runs") ||
    /\/monitor\/runs\/\d+\/attempts$/.test(path) ||
    path.endsWith("/projects/v2-copper-demo/reviewers") ||
    path.endsWith("/projects/v2-copper-demo/experiments/99/review-sets") ||
    /\/review-sets\/88\/reviewers\/[^/]+\/queue$/.test(path) ||
    path.endsWith("/monitor/experiments/99/analytics") ||
    path.includes("/monitor/experiments/99/analytics?")
  );
}

function v2RunFixtures() {
  const modelSlugs = ["openai_gpt_high", "claude_high"];
  const promptSlugs = ["expert_investment_analyst_v3", "general_finance_assistant_v2"];
  const warmerSlugs = [
    "none",
    "copper_expert_user_v2",
    "copper_low_knowledge_user_v1",
    "copper_adversarial_user_v1",
  ];
  return Array.from({ length: 16 }, (_, index) => ({
    id: 5000 + index + 1,
    run_id: `v2-run-${index + 1}`,
    experiment_id: 99,
    case_slug: "chile_copper_memo",
    model_config_slug: modelSlugs[index % modelSlugs.length],
    system_prompt_slug: promptSlugs[Math.floor(index / 2) % promptSlugs.length],
    warmer_slug: warmerSlugs[Math.floor(index / 4) % warmerSlugs.length],
    status: "complete",
  }));
}

function v2AttemptFixtures(runId: number) {
  return [0, 1].map((replicateIndex) => ({
    id: runId * 10 + replicateIndex,
    run_id: runId,
    attempt_id: `v2-attempt-${runId}-${replicateIndex}`,
    replicate_index: replicateIndex,
    attempt_number: replicateIndex + 1,
    parent_attempt_id: null,
    status: "succeeded",
    error_kind: null,
    error_message: null,
    terminal_failure_reason: null,
    provider_response_id: null,
    input_tokens: 900 + replicateIndex * 10,
    output_tokens: 420 + replicateIndex * 10,
    total_tokens: 1320 + replicateIndex * 20,
    cost_usd: 0.12 + replicateIndex * 0.02,
    latency_ms: 800 + replicateIndex * 50,
    started_at: "2026-05-22T02:00:00Z",
    completed_at: "2026-05-22T02:00:01Z",
    request_payload: { mode: "local_only_synthetic" },
    response_payload: { demo_id: "v2_copper_demo" },
    cache_key: null,
    cache_hit: false,
  }));
}

function v2ReviewSetFixture(revealed: boolean) {
  return {
    id: 88,
    slug: "v2-copper-demo-review",
    name: "V2 copper demo multi-reviewer calibration",
    review_type: "blind_pairwise",
    metadata: {
      failure_tags: [
        "unsupported supply claim",
        "missing citation",
        "weak conclusion",
        "overstated certainty",
      ],
      failure_taxonomy: {
        slug: "v2_copper_failure_taxonomy",
        name: "V2 copper failure taxonomy",
        version: 1,
        tags: [
          "unsupported supply claim",
          "missing citation",
          "weak conclusion",
          "overstated certainty",
        ],
      },
    },
    assignment_progress: {
      assigned: 32,
      submitted: 32,
      pending: 0,
    },
    items: [
      {
        id: 880,
        item_key: "v2_copper_demo_pair_1",
        prompt: { case_slug: "chile_copper_memo" },
        answers: [
          { label: "A", run_attempt_id: 50101, text: "V2 local answer A" },
          { label: "B", run_attempt_id: 50102, text: "V2 local answer B" },
        ],
        reviewer_decision: {},
        ...(revealed
          ? {
              reveal_metadata: {
                answers: [
                  {
                    label: "A",
                    model_config_slug: "openai_gpt_high",
                    system_prompt_slug: "expert_investment_analyst_v3",
                    warmer_slug: "none",
                    cost_usd: 0.12,
                  },
                  {
                    label: "B",
                    model_config_slug: "claude_high",
                    system_prompt_slug: "expert_investment_analyst_v3",
                    warmer_slug: "none",
                    cost_usd: 0.14,
                  },
                ],
              },
            }
          : {}),
      },
    ],
  };
}

function v2ReviewQueueFixture(path: string) {
  const reviewerSlug = decodeURIComponent(path.match(/\/reviewers\/([^/]+)\/queue$/)?.[1] ?? "");
  const reviewSet = v2ReviewSetFixture(false);
  return {
    review_set: {
      id: reviewSet.id,
      slug: reviewSet.slug,
      name: reviewSet.name,
      review_type: reviewSet.review_type,
    },
    reviewer: {
      id: 10,
      slug: reviewerSlug,
      name: reviewerSlug,
      email: null,
    },
    failure_taxonomy: reviewSet.metadata.failure_taxonomy,
    progress: reviewSet.assignment_progress,
    items: reviewSet.items.map((item, index) => ({
      ...item,
      assignment_id: 1880 + index,
      assignment_status: "submitted",
    })),
  };
}

function resultsFetch(requests: Array<{ method?: string; url: string; body?: string }>) {
  return async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    requests.push({ method: init?.method ?? "GET", url: path, body: init?.body?.toString() });
    if (path.endsWith("/experiments/drafts")) {
      return jsonResponse({
        id: 42,
        slug: "copper_memo_context_sensitivity",
        name: "Copper memo context sensitivity",
        status: "complete",
      });
    }
    if (
      path.endsWith("/monitor/experiments/42/analytics") ||
      path.includes("/monitor/experiments/42/analytics?")
    ) {
      return jsonResponse(resultsAnalyticsFixture());
    }
    if (path.endsWith("/monitor/experiments/42/metric-adapters/run")) {
      return jsonResponse({
        status: "completed",
        scores_recorded: 1,
        planned_scores: 0,
        skipped: [],
      });
    }
    if (
      path.includes("/monitor/experiments/42/exports?") &&
      path.includes("format=promptfoo")
    ) {
      return jsonResponse({
        format: "promptfoo",
        content: "description: Promptfoo export\n",
        warnings: [
          {
            code: "unsupported_evaluator_mapping",
            path: "$.evaluation.evaluators.unsupported",
            message: "Unsupported evaluator mapping.",
          },
        ],
      });
    }
    if (
      path.includes("/monitor/experiments/42/exports?") &&
      path.includes("format=otel-json")
    ) {
      return jsonResponse({
        format: "otel-json",
        content: '{\n  "format_version": "model_eval_otel_trace_v1",\n  "spans": []\n}',
        warnings: [],
      });
    }
    if (path.endsWith("/projects/default/imports/promptfoo/preview")) {
      const persist = init?.body?.toString().includes('"persist":true') ?? false;
      return jsonResponse({
        manifest: { name: "Promptfoo import" },
        preview: { logical_runs: 1, run_attempts: 1 },
        warnings: [
          {
            code: "ambiguous_prompt_shape",
            path: "$.prompts[0]",
            message: "Ambiguous prompt shape.",
          },
        ],
        library_records: { metric_adapter_configs: [] },
        persisted: persist
          ? {
              project_slug: "default",
              created: {
                cases: 1,
                system_prompts: 1,
                warmers: 1,
                model_configs: 1,
                evaluators: 1,
                metric_adapter_configs: 0,
              },
            }
          : undefined,
      });
    }
    return jsonResponse({ id: 1, slug: "ok" }, 201);
  };
}

function v2ResultsAnalyticsFixture() {
  const base = resultsAnalyticsFixture();
  const [baseFrontierRow, baseDominatedRow] = base.cost_quality_frontier;
  const v2FrontierKey =
    "chile_copper_memo|v2_copper_benchmark_suite|all|openai_gpt_high|expert_investment_analyst_v3|none";
  const v2JudgeDivergence = {
    ...base.divergence_summary[1],
    case_slug: "chile_copper_memo",
    model_config_slug: "openai_gpt_high",
    system_prompt_slug: "expert_investment_analyst_v3",
    warmer_slug: "none",
    criterion: "divergence_conclusion",
    metric_source: "llm_judge_rubric",
    source_kind: "judge_backed",
    label: "low",
    sample_count: 16,
    confidence: 0.78,
  };
  return {
    ...base,
    experiment_id: 99,
    summary: {
      ...base.summary,
      attempt_count: 32,
      failed_attempt_count: 0,
      failure_rate: 0,
      average_cost_usd: 0.14,
      average_latency_ms: 850,
      token_totals: {
        input_tokens: 29120,
        output_tokens: 13760,
        total_tokens: 42880,
      },
    },
    failure_tag_frequency: [{ tag: "missing citation", count: 4, rate: 0.125 }],
    cost_quality_frontier: [
      {
        ...baseFrontierRow,
        frontier_key: v2FrontierKey,
        case_slug: "chile_copper_memo",
        suite_slug: "v2_copper_benchmark_suite",
        suite_split: "all",
        model_config_slug: "openai_gpt_high",
        system_prompt_slug: "expert_investment_analyst_v3",
        warmer_slug: "none",
        attempt_count: 16,
        failed_attempt_count: 0,
        quality_rate: 0.84,
        average_cost_usd: 0.12,
        average_latency_ms: 800,
        divergence_summary: [v2JudgeDivergence],
        judge_calibration_overlays: [
          {
            ...baseFrontierRow.judge_calibration_overlays[0],
            evaluator_id: "v2_synthetic_judge",
            comparison_count: 16,
            agreement_rate: 0.78,
            low_confidence_count: 2,
          },
        ],
        dominated_by: null,
        is_frontier: true,
        dominance_status: "frontier",
        promptfoo_provider_id: "openai:gpt-5.5",
        promptfoo_prompt_id: "expert_investment_analyst_v3",
        promptfoo_test_description: "Chile Copper Memo",
      },
      {
        ...baseDominatedRow,
        frontier_key:
          "chile_copper_memo|v2_copper_benchmark_suite|all|claude_high|expert_investment_analyst_v3|copper_expert_user_v2",
        case_slug: "chile_copper_memo",
        suite_slug: "v2_copper_benchmark_suite",
        suite_split: "all",
        model_config_slug: "claude_high",
        system_prompt_slug: "expert_investment_analyst_v3",
        warmer_slug: "copper_expert_user_v2",
        attempt_count: 16,
        failed_attempt_count: 0,
        quality_rate: 0.63,
        average_cost_usd: 0.16,
        average_latency_ms: 900,
        dominated_by: v2FrontierKey,
        is_frontier: false,
        dominance_status: "dominated",
        promptfoo_provider_id: "anthropic:claude",
        promptfoo_prompt_id: "expert_investment_analyst_v3",
        promptfoo_test_description: "Chile Copper Memo",
      },
    ],
    divergence_summary: [v2JudgeDivergence],
    carryover_summary: [
      {
        ...base.carryover_summary[0],
        case_slug: "chile_copper_memo",
        model_config_slug: "openai_gpt_high",
        system_prompt_slug: "expert_investment_analyst_v3",
        warmer_slug: "copper_expert_user_v2",
        sample_count: 16,
      },
    ],
    reviewer_coverage: [
      {
        review_set_id: 88,
        assigned_count: 32,
        submitted_count: 32,
        pending_count: 0,
        reviewer_count: 2,
        coverage_rate: 1,
      },
    ],
    reviewer_disagreement: [
      {
        review_item_id: 880,
        review_set_id: 88,
        reviewer_count: 2,
        pairwise_disagreement: true,
        pass_fail_disagreement_count: 1,
        failure_tag_disagreement_count: 1,
      },
    ],
    judge_calibration: [
      {
        evaluator_id: "v2_synthetic_judge",
        comparison_count: 16,
        pairwise_comparison_count: 16,
        pairwise_agreement_count: 13,
        pass_fail_comparison_count: 16,
        pass_fail_agreement_count: 12,
        rubric_comparison_count: 16,
        rubric_agreement_count: 12,
        agreement_count: 37,
        disagreement_count: 11,
        agreement_rate: 0.78,
        low_confidence_count: 2,
      },
    ],
    metric_adapter_scores: [
      {
        ...base.metric_adapter_scores[0],
        attempt_id: "v2-attempt-5001-0",
        case_slug: "chile_copper_memo",
        model_config_slug: "openai_gpt_high",
        system_prompt_slug: "expert_investment_analyst_v3",
        warmer_slug: "none",
        adapter_config_slug: "v2_retrieval_precision",
        adapter_config_version: 1,
        criterion: "retrieval_precision",
        score: 0.86,
        label: "strong",
        explanation: "Synthetic local retrieval precision over fixture chunks.",
      },
    ],
  };
}

function resultsAnalyticsFixture() {
  return {
    experiment_id: 42,
    filters: {
      case_slug: null,
      suite_slug: null,
      suite_split: null,
      model_config_slug: null,
      system_prompt_slug: null,
      warmer_slug: null,
      evaluator_source: null,
      reviewer_id: null,
    },
    summary: {
      attempt_count: 4,
      failed_attempt_count: 1,
      failure_rate: 0.25,
      winner_count: 1,
      loser_count: 1,
      tie_count: 1,
      cannot_judge_count: 0,
      win_rate: 0.5,
      pass_count: 2,
      fail_count: 1,
      pass_rate: 2 / 3,
      average_cost_usd: 0.3,
      average_latency_ms: 1500,
      token_totals: {
        input_tokens: 330,
        output_tokens: 155,
        total_tokens: 485,
      },
    },
    failure_tag_frequency: [{ tag: "too generic", count: 2, rate: 0.5 }],
    warmer_lift: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "brief",
        metric: "pass_rate",
        baseline_warmer_slug: "none",
        baseline_missing: false,
        baseline_rate: 0.5,
        warmer_rate: 0.55,
        lift: 0.05,
      },
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        metric: "pass_rate",
        baseline_warmer_slug: "none",
        baseline_missing: false,
        baseline_rate: 0.5,
        warmer_rate: 0.75,
        lift: 0.25,
      },
    ],
    context_sensitivity: [
      {
        case_slug: "other",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_count: 2,
        scored_warmer_count: 2,
        metric: "pass_rate",
        best_warmer_slug: "analyst",
        worst_warmer_slug: "none",
        score_spread: 0.1,
        label: "low",
      },
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_count: 2,
        scored_warmer_count: 2,
        metric: "pass_rate",
        best_warmer_slug: "analyst",
        worst_warmer_slug: "none",
        score_spread: 0.5,
        label: "high",
      },
    ],
    divergence_placeholders: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        score_spread: 0.5,
        failure_tag_spread: true,
        signals: ["score_spread", "failure_tag_spread"],
        label: "high",
        semantic_diff_available: false,
      },
    ],
    divergence_metrics: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        criterion: "divergence_claim",
        metric_source: "deterministic_fallback",
        source_kind: "deterministic_heuristic",
        comparison_scope: "case_model_system_prompt_warmer",
        baseline_attempt_id: "base-1",
        comparison_attempt_id: "warm-1",
        value: 0.42,
        label: "medium",
        warning: "No judge-backed claim evidence is available; deterministic fallback uses local text heuristics only.",
        warning_label: "heuristic",
        sample_count: 1,
        confidence: 0.35,
        explanation: "Compared local claim text with deterministic fallback heuristics.",
        details: {},
      },
    ],
    divergence_summary: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        criterion: "divergence_claim",
        metric_source: "deterministic_fallback",
        source_kind: "deterministic_heuristic",
        value: 0.42,
        label: "medium",
        warning: "No judge-backed claim evidence is available; deterministic fallback uses local text heuristics only.",
        warning_label: "heuristic",
        sample_count: 2,
        confidence: 0.35,
      },
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        criterion: "divergence_conclusion",
        metric_source: "llm_judge_rubric",
        source_kind: "judge_backed",
        value: 0.2,
        label: "medium",
        warning: "Judge-backed divergence uses existing stored LLM judge scores and should be calibrated against human labels before being treated as a quality signal.",
        warning_label: "judge_needs_calibration",
        sample_count: 1,
        confidence: 0.7,
      },
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        criterion: "divergence_failure_mode_spread",
        metric_source: "human_failure_tags",
        source_kind: "human_backed",
        value: 0.5,
        label: "high",
        warning: "Failure-mode spread is based on available human failure tags, not semantic judging.",
        warning_label: "human_labeled",
        sample_count: 1,
        confidence: 1,
      },
    ],
    carryover_audit: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        comparison_attempt_id: "warm-1",
        source_evidence: "local_warmer_overlap",
        source_kind: "deterministic_heuristic",
        status: "reused",
        explanation: "Output reuses locally matched warmer terms.",
        warning: "Carryover audit uses local warmer/output token overlap only.",
        warning_label: "heuristic",
        sample_count: 1,
        confidence: 0.4,
        details: {},
      },
    ],
    carryover_summary: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        source_evidence: "local_warmer_overlap",
        source_kind: "deterministic_heuristic",
        status: "reused",
        warning: "Carryover audit uses local warmer/output token overlap only.",
        warning_label: "heuristic",
        sample_count: 2,
        confidence: 0.4,
      },
    ],
    cost_quality_table: [
      {
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "none",
        attempt_count: 2,
        win_rate: 0.5,
        pass_rate: 0.5,
        failure_rate: 0,
        average_cost_usd: 0.2,
        average_latency_ms: 1000,
        token_totals: { input_tokens: 100, output_tokens: 40, total_tokens: 140 },
        quality_metric: "pass_rate",
        quality_rate: 0.5,
        cost_usd_per_quality_point: 0.4,
      },
      {
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        attempt_count: 2,
        win_rate: 0.75,
        pass_rate: 0.75,
        failure_rate: 0.25,
        average_cost_usd: 0.4,
        average_latency_ms: 2000,
        token_totals: { input_tokens: 230, output_tokens: 115, total_tokens: 345 },
        quality_metric: "pass_rate",
        quality_rate: 0.75,
        cost_usd_per_quality_point: 0.5333333333,
      },
    ],
    latency_quality_table: [
      {
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        attempt_count: 2,
        win_rate: 0.75,
        pass_rate: 0.75,
        failure_rate: 0.25,
        average_cost_usd: 0.4,
        average_latency_ms: 2000,
        token_totals: { input_tokens: 230, output_tokens: 115, total_tokens: 345 },
        quality_metric: "pass_rate",
        quality_rate: 0.75,
      },
    ],
    cost_quality_frontier: [
      {
        frontier_key: "case|suite_a|holdout|model_a|system|none",
        case_slug: "case",
        suite_slug: "suite_a",
        suite_split: "holdout",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "none",
        attempt_count: 2,
        failed_attempt_count: 0,
        quality_metric: "pass_rate",
        quality_rate: 0.8,
        quality_interval: {
          sample_count: 2,
          rate: 0.8,
          lower: 0.4,
          upper: 1,
          label: "low_sample",
        },
        quality_uncertainty_label: "low_sample",
        average_cost_usd: 0.2,
        cost_usd_interval: {
          sample_count: 1,
          mean: 0.2,
          variance: 0,
          lower: 0.2,
          upper: 0.2,
          label: "single_sample",
        },
        cost_uncertainty_label: "single_sample",
        average_latency_ms: 900,
        latency_ms_interval: {
          sample_count: 1,
          mean: 900,
          variance: 0,
          lower: 900,
          upper: 900,
          label: "single_sample",
        },
        latency_uncertainty_label: "single_sample",
        token_totals: { input_tokens: 100, output_tokens: 40, total_tokens: 140 },
        total_tokens_interval: {
          sample_count: 1,
          mean: 140,
          variance: 0,
          lower: 140,
          upper: 140,
          label: "single_sample",
        },
        warmer_lift: null,
        divergence_summary: [],
        carryover_summary: [],
        judge_calibration_overlays: [
          {
            evaluator_id: "judge_1",
            comparison_count: 1,
            agreement_rate: 1,
            low_confidence_count: 0,
          },
        ],
        dominated_by: null,
        is_frontier: true,
        dominance_status: "frontier",
        promptfoo_provider_id: "openai:gpt-5.5",
        promptfoo_prompt_id: "system",
        promptfoo_test_description: "Case",
        promptfoo_assertion_types: ["not-empty"],
      },
      {
        frontier_key: "case|suite_a|holdout|model_b|system|analyst",
        case_slug: "case",
        suite_slug: "suite_a",
        suite_split: "holdout",
        model_config_slug: "model_b",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        attempt_count: 2,
        failed_attempt_count: 1,
        quality_metric: "pass_rate",
        quality_rate: 0.6,
        quality_interval: {
          sample_count: 2,
          rate: 0.6,
          lower: 0.2,
          upper: 0.9,
          label: "low_sample",
        },
        quality_uncertainty_label: "low_sample",
        average_cost_usd: 0.4,
        cost_usd_interval: {
          sample_count: 1,
          mean: 0.4,
          variance: 0,
          lower: 0.4,
          upper: 0.4,
          label: "single_sample",
        },
        cost_uncertainty_label: "single_sample",
        average_latency_ms: 1400,
        latency_ms_interval: {
          sample_count: 1,
          mean: 1400,
          variance: 0,
          lower: 1400,
          upper: 1400,
          label: "single_sample",
        },
        latency_uncertainty_label: "single_sample",
        token_totals: { input_tokens: 130, output_tokens: 60, total_tokens: 190 },
        total_tokens_interval: {
          sample_count: 1,
          mean: 190,
          variance: 0,
          lower: 190,
          upper: 190,
          label: "single_sample",
        },
        warmer_lift: {
          case_slug: "case",
          model_config_slug: "model_b",
          system_prompt_slug: "system",
          warmer_slug: "analyst",
          metric: "pass_rate",
          baseline_warmer_slug: "none",
          baseline_missing: false,
          baseline_rate: 0.5,
          warmer_rate: 0.6,
          lift: 0.1,
        },
        divergence_summary: [
          {
            case_slug: "case",
            model_config_slug: "model_b",
            system_prompt_slug: "system",
            warmer_slug: "analyst",
            criterion: "divergence_claim",
            metric_source: "deterministic_fallback",
            source_kind: "deterministic_heuristic",
            value: 0.3,
            label: "medium",
            warning: "Deterministic fallback.",
            warning_label: "heuristic",
            sample_count: 1,
            confidence: 0.4,
          },
        ],
        carryover_summary: [],
        judge_calibration_overlays: [],
        dominated_by: "case|suite_a|holdout|model_a|system|none",
        is_frontier: false,
        dominance_status: "dominated",
        promptfoo_provider_id: "anthropic:claude",
        promptfoo_prompt_id: "system",
        promptfoo_test_description: "Case",
        promptfoo_assertion_types: ["not-empty"],
      },
    ],
    failure_rate_table: [
      {
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        attempt_count: 2,
        failed_attempt_count: 1,
        failure_rate: 0.5,
      },
    ],
    failure_rate_by_dimension: {},
    nondeterminism_by_dimension: {
      model_config_slug: [
        {
          model_config_slug: "model_a",
          sample_count: 2,
          retry_attempt_count: 1,
          failure_rate_interval: {
            sample_count: 2,
            rate: 0.5,
            lower: 0.1,
            upper: 0.9,
            label: "low_sample",
          },
          pass_rate_interval: {
            sample_count: 2,
            rate: 0.5,
            lower: 0.1,
            upper: 0.9,
            label: "low_sample",
          },
          win_rate_interval: {
            sample_count: 0,
            rate: null,
            lower: null,
            upper: null,
            label: "no_samples",
          },
          cost_usd_interval: {
            sample_count: 0,
            mean: null,
            variance: null,
            lower: null,
            upper: null,
            label: "no_samples",
          },
          latency_ms_interval: {
            sample_count: 2,
            mean: 1500,
            variance: 10000,
            lower: 1200,
            upper: 1800,
            label: "low_sample",
          },
          total_tokens_interval: {
            sample_count: 2,
            mean: 242.5,
            variance: 100,
            lower: 200,
            upper: 285,
            label: "low_sample",
          },
        },
      ],
    },
    judge_calibration: [],
    judge_verbosity_bias: [],
    reviewer_coverage: [
      {
        review_set_id: 7,
        assigned_count: 4,
        submitted_count: 3,
        pending_count: 1,
        reviewer_count: 2,
        coverage_rate: 0.75,
      },
    ],
    reviewer_disagreement: [
      {
        review_item_id: 70,
        review_set_id: 7,
        reviewer_count: 2,
        pairwise_disagreement: true,
        pass_fail_disagreement_count: 1,
        failure_tag_disagreement_count: 1,
      },
    ],
    failure_taxonomy_rollup: [{ tag: "too generic", taxonomy_version: 1, count: 2 }],
    metric_adapter_scores: [
      {
        attempt_id: "attempt-1",
        case_slug: "case",
        model_config_slug: "model_a",
        system_prompt_slug: "system",
        warmer_slug: "analyst",
        adapter_config_slug: "retrieval_precision_local",
        adapter_config_version: 1,
        criterion: "retrieval_precision",
        metric_source: "local_metric_adapter",
        source_kind: "deterministic_heuristic",
        score: 0.82,
        label: "strong",
        explanation: "Measured retrieved chunk lexical overlap with the answer text.",
        confidence: 0.7,
      },
    ],
  };
}

function reviewFetch(
  requests: Array<{ method?: string; url: string; body?: string }>,
  options: { createConflict?: boolean } = {},
) {
  return async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    requests.push({ method: init?.method ?? "GET", url: path, body: init?.body?.toString() });
    if (path.endsWith("/experiments/drafts")) {
      return jsonResponse({
        id: 42,
        slug: "copper_memo_context_sensitivity",
        name: "Copper memo context sensitivity",
        status: "complete",
      });
    }
    if (path.endsWith("/projects/default/reviewers")) {
      return jsonResponse(reviewerFixture(init), 201);
    }
    if (
      path.endsWith(
        "/experiments/42/review-sets?slug=copper_memo_context_sensitivity-human-review",
      )
    ) {
      return jsonResponse([reviewSetFixture(false)]);
    }
    if (path.endsWith("/experiments/42/review-sets")) {
      if (options.createConflict) {
        return jsonResponse({ detail: "Resource already exists." }, 409);
      }
      return jsonResponse(reviewSetFixture(false), 201);
    }
    if (path.endsWith("/review-sets/7?reveal_metadata=true")) {
      return jsonResponse(reviewSetFixture(true));
    }
    if (path.match(/\/review-sets\/7\/reviewers\/[^/]+\/queue$/)) {
      return jsonResponse(reviewQueueFixture(path));
    }
    if (path.endsWith("/review-sets/7/assignments")) {
      return jsonResponse(reviewAssignmentsFixture(init), 201);
    }
    if (path.endsWith("/review-assignments/170/decision")) {
      return jsonResponse({
        id: 170,
        review_set_id: 7,
        review_item_id: 70,
        status: "submitted",
        reviewer: reviewerFixture(init),
      });
    }
    if (path.endsWith("/review-items/70/decision")) {
      return jsonResponse({
        id: 70,
        reviewer_decision: JSON.parse(init?.body?.toString() || "{}"),
      });
    }
    return jsonResponse({ id: 1, slug: "ok" }, 201);
  };
}

function reviewSetFixture(revealed: boolean) {
  return {
    id: 7,
    slug: "copper_memo_context_sensitivity-human-review",
    name: "Copper memo context sensitivity human review",
    review_type: "blind_pairwise",
    metadata: {
      failure_tags: [
        "too generic",
        "missed transmission mechanism",
        "no quantified impact",
        "weak risks",
      ],
      failure_taxonomy: {
        slug: "copper_memo_defaults",
        name: "Copper memo defaults",
        version: 1,
        tags: [
          "too generic",
          "missed transmission mechanism",
          "no quantified impact",
          "weak risks",
        ],
      },
    },
    assignment_progress: {
      assigned: 2,
      submitted: 0,
      pending: 2,
    },
    items: [
      {
        id: 70,
        item_key: "case__system__warmer__0__pair_1",
        prompt: { case_slug: "case" },
        answers: [
          { label: "A", run_attempt_id: 101, text: "blind output A" },
          { label: "B", run_attempt_id: 102, text: "blind output B" },
        ],
        reviewer_decision: {},
        ...(revealed
          ? {
              reveal_metadata: {
                answers: [
                  {
                    label: "A",
                    model_config_slug: "model_a",
                    system_prompt_slug: "system",
                    warmer_slug: "warmer",
                    cost_usd: 0.12,
                  },
                  {
                    label: "B",
                    model_config_slug: "model_b",
                    system_prompt_slug: "system",
                    warmer_slug: "warmer",
                    cost_usd: 0.18,
                  },
                ],
              },
            }
          : {}),
      },
      {
        id: 71,
        item_key: "case__system__warmer__0__pair_2",
        prompt: { case_slug: "case" },
        answers: [
          { label: "A", run_attempt_id: 103, text: "second blind output A" },
          { label: "B", run_attempt_id: 104, text: "second blind output B" },
        ],
        reviewer_decision: {},
        ...(revealed
          ? {
              reveal_metadata: {
                answers: [
                  {
                    label: "A",
                    model_config_slug: "model_c",
                    system_prompt_slug: "system",
                    warmer_slug: "warmer",
                    cost_usd: 0.14,
                  },
                  {
                    label: "B",
                    model_config_slug: "model_d",
                    system_prompt_slug: "system",
                    warmer_slug: "warmer",
                    cost_usd: 0.16,
                  },
                ],
              },
            }
          : {}),
      },
    ],
  };
}

function reviewQueueFixture(path: string) {
  const reviewerSlug = decodeURIComponent(path.match(/\/reviewers\/([^/]+)\/queue$/)?.[1] ?? "");
  const reviewSet = reviewSetFixture(false);
  return {
    review_set: {
      id: reviewSet.id,
      slug: reviewSet.slug,
      name: reviewSet.name,
      review_type: reviewSet.review_type,
    },
    reviewer: {
      id: 9,
      slug: reviewerSlug,
      name: reviewerSlug,
      email: null,
    },
    failure_taxonomy: reviewSet.metadata.failure_taxonomy,
    progress: {
      assigned: 2,
      submitted: 0,
      pending: 2,
    },
    items: reviewSet.items.map((item, index) => ({
      ...item,
      assignment_id: 170 + index,
      assignment_status: "pending",
    })),
  };
}

function reviewAssignmentsFixture(init?: RequestInit) {
  const payload = JSON.parse(init?.body?.toString() || "{}") as { reviewer_slugs?: string[] };
  const reviewerSlug = payload.reviewer_slugs?.[0] ?? "human-test";
  return {
    review_set_id: 7,
    assignment_progress: {
      assigned: 2,
      submitted: 0,
      pending: 2,
    },
    assignments: [170, 171].map((id, index) => ({
      id,
      review_set_id: 7,
      review_item_id: 70 + index,
      status: "pending",
      reviewer: {
        id: 9,
        slug: reviewerSlug,
        name: reviewerSlug,
        email: null,
      },
    })),
  };
}

function reviewerFixture(init?: RequestInit) {
  const payload = JSON.parse(init?.body?.toString() || "{}") as { slug?: string; name?: string };
  return {
    id: 9,
    slug: payload.slug ?? "human-test",
    name: payload.name ?? payload.slug ?? "human-test",
    email: null,
  };
}

function runFixture(id: number, caseSlug: string, status: string) {
  return {
    id,
    run_id: `run-${id}`,
    experiment_id: 42,
    case_slug: caseSlug,
    model_config_slug: id % 2 ? "openai_gpt_high" : "anthropic_opus",
    system_prompt_slug: "expert_prompt",
    warmer_slug: id === 1 ? "none" : "copper_warmer",
    status,
  };
}

function attemptFixture(runId: number) {
  const failed = runId === 3;
  const providerBlocked = runId === 6;
  return {
    id: runId,
    run_id: runId,
    attempt_id: `attempt-${runId}`,
    replicate_index: 0,
    attempt_number: 1,
    parent_attempt_id: null,
    status: failed || providerBlocked ? "failed" : runId === 4 ? "succeeded" : "queued",
    error_kind: failed || providerBlocked ? "blocked_by_config" : null,
    error_message: failed
      ? "Cost cap exceeded before provider call."
      : providerBlocked
        ? "Provider is blocked by allow/deny configuration."
        : null,
    terminal_failure_reason: failed
      ? "cost_cap_exceeded"
      : providerBlocked
        ? "provider_blocked"
        : null,
    provider_response_id: failed ? "resp-cost-cap" : null,
    input_tokens: failed ? 800 : 100,
    output_tokens: failed ? 400 : 50,
    total_tokens: failed ? 1200 : 150,
    cost_usd: failed ? 0.42 : runId === 4 ? 0.31 : 0,
    latency_ms: failed ? 1234 : runId === 4 ? 980 : null,
    started_at: failed ? "2026-05-20T12:00:00Z" : null,
    completed_at: failed ? "2026-05-20T12:00:01Z" : null,
    request_payload: failed ? { model: "gpt-5.5", temperature: 0.2 } : {},
    response_payload: failed ? { blocked: true } : {},
    cache_key: null,
    cache_hit: false,
  };
}

function jsonResponse(payload: object, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}
