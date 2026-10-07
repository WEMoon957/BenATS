import "@testing-library/jest-dom/vitest";

// 本测试环境里 DOM 侧的 Blob/File/FormData 由 jsdom 提供，而 Response 由 Node 提供，两者不兼容：
// 1) jsdom Blob 未实现 stream()，以它作响应体会抛 "object.stream is not a function"；
// 2) response.blob() 返回的是 Node Blob，与全局 Blob（jsdom）不是同一个类，expect.any(Blob) 恒为假。
// 以下两处让 Node 的 Response 与 jsdom 的 Blob 双向对齐，保持环境中只有一套 Blob 语义。

// 1) 为 jsdom Blob 补齐 stream()，使其可作为 Response 的响应体。
if (typeof Blob.prototype.stream !== "function") {
  const stream = function stream(this: Blob): ReadableStream<Uint8Array> {
    const blob = this;
    return new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(new Uint8Array(await blob.arrayBuffer()));
        controller.close();
      },
    });
  };
  Blob.prototype.stream = stream as unknown as typeof Blob.prototype.stream;
}

// 2) 让 response.blob() 返回本环境的 Blob（jsdom），与 FormData / File 的类型判断保持一致。
const nativeResponseBlob = Response.prototype.blob;
Response.prototype.blob = async function blob(this: Response): Promise<Blob> {
  const result = await nativeResponseBlob.call(this);
  return new Blob([new Uint8Array(await result.arrayBuffer())], { type: result.type });
};
