import { afterEach, describe, expect, it, vi } from "vitest";
import {
  browserLangGraphApiUrl,
  getBackendStatus,
  isLangGraphInfo,
  loadClientConfig,
  revealPathInFileManager,
} from "./backend";
import { DEFAULT_BACKEND_PORT } from "./types";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (globalThis as { isTauri?: boolean }).isTauri;
});

describe("artifact path opening", () => {
  it("opens the same-origin H5 preview route", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    vi.stubGlobal("location", { origin: "https://agent.example.test" });

    await revealPathInFileManager("/repo", "/repo/project", "test_case/a.spec.ts");

    expect(open).toHaveBeenCalledOnce();
    const target = new URL(open.mock.calls[0][0]);
    expect(target.pathname).toBe("/api/artifacts/preview");
    expect(target.searchParams.get("path")).toBe("test_case/a.spec.ts");
    expect(target.searchParams.get("base_dir")).toBe("/repo/project");
  });

  it("passes the root, base directory and path to the Tauri command", async () => {
    const invoke = vi.fn().mockResolvedValue(undefined);
    Object.assign(globalThis, { isTauri: true });
    vi.stubGlobal("window", { __TAURI_INTERNALS__: { invoke } });

    await revealPathInFileManager("/repo", "/repo/project", "test_case/a.spec.ts");

    expect(invoke).toHaveBeenCalledWith(
      "reveal_path_in_file_manager",
      {
        projectRoot: "/repo",
        baseDir: "/repo/project",
        path: "test_case/a.spec.ts",
      },
      undefined,
    );
  });
});

describe("stored client config", () => {
  it.each([1, 1023, 65536, 70000, 2024.5])(
    "replaces an out-of-range stored port %s",
    (backendPort) => {
      vi.stubGlobal("localStorage", {
        getItem: () => JSON.stringify({ projectRoot: "/repo", backendPort }),
      });

      expect(loadClientConfig()).toEqual({
        projectRoot: "/repo",
        backendPort: DEFAULT_BACKEND_PORT,
      });
    },
  );

  it("keeps a valid stored port", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => JSON.stringify({ projectRoot: "/repo", backendPort: 65535 }),
    });

    expect(loadClientConfig().backendPort).toBe(65535);
  });
});

describe("LangGraph /info validation", () => {
  it("accepts the LangGraph runtime payload", () => {
    expect(
      isLangGraphInfo({
        version: "0.11.0",
        langgraph_py_version: "1.1.9",
        flags: { assistants: true },
      }),
    ).toBe(true);
  });

  it.each([
    null,
    "not-json",
    { status: "ok", flags: {} },
    { langgraph_py_version: "1.1.9" },
    { langgraph_py_version: "", flags: {} },
  ])("rejects a non-LangGraph payload", (payload) => {
    expect(isLangGraphInfo(payload)).toBe(false);
  });
});

describe("browser backend probe", () => {
  it("uses the browser origin for the LangGraph API", () => {
    vi.stubGlobal("location", { origin: "https://agent.example.test" });
    expect(browserLangGraphApiUrl()).toBe("https://agent.example.test/api/langgraph");
  });

  it("stops waiting for an unresponsive /info endpoint", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
      ),
    );

    const statusPromise = getBackendStatus({ projectRoot: "/repo", backendPort: 2024 });
    await vi.advanceTimersByTimeAsync(2_000);

    await expect(statusPromise).resolves.toMatchObject({ state: "stopped" });
  });
});
