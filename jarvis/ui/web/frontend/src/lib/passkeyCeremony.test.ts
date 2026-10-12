import { describe, expect, it, vi } from "vitest";

import {
  base64UrlToBinary,
  binaryToBase64Url,
  creationOptionsFromJSON,
  registerOwnerPasskey,
  requestOptionsFromJSON,
  verifyOwnerPasskey,
  type PasskeyCreationOptionsJSON,
  type PasskeyRequestOptionsJSON,
} from "@/lib/passkeyCeremony";

const bytes = (...numbers: number[]) => Uint8Array.from(numbers).buffer as ArrayBuffer;
const asBytes = (value: BufferSource) => Array.from(
  value instanceof ArrayBuffer
    ? new Uint8Array(value)
    : new Uint8Array(value.buffer, value.byteOffset, value.byteLength),
);

const enrollment: PasskeyCreationOptionsJSON = {
  rp: { name: "AERION", id: "localhost" },
  challenge: "_wD7EA",
  user: { id: "AAE", name: "owner", displayName: "Owner" },
  pubKeyCredParams: [{ type: "public-key", alg: -7 }],
  timeout: 120_000,
  excludeCredentials: [{ id: "_wD7EA", type: "public-key", transports: ["internal"] }],
  authenticatorSelection: { userVerification: "required" },
};

const approval: PasskeyRequestOptionsJSON = {
  challenge: "_wD7EA",
  rpId: "localhost",
  allowCredentials: [{ id: "AAE", type: "public-key", transports: ["internal"] }],
  userVerification: "required",
};

describe("browser passkey ceremony", () => {
  it("round trips WebAuthn binary values with URL-safe encoding", () => {
    const input = bytes(255, 0, 251, 16);
    expect(binaryToBase64Url(input)).toBe("_wD7EA");
    expect(Array.from(new Uint8Array(base64UrlToBinary("_wD7EA"))))
      .toEqual([255, 0, 251, 16]);
  });

  it.each(["", "$$$", "a", "broken!", "ab=c"])(
    "rejects invalid binary input %s",
    (input) => expect(() => base64UrlToBinary(input)).toThrow(),
  );

  it("maps the server challenge and credential identifiers to ArrayBuffers", () => {
    const creation = creationOptionsFromJSON(enrollment);
    expect(asBytes(creation.challenge)).toEqual([255, 0, 251, 16]);
    expect(asBytes(creation.user.id)).toEqual([0, 1]);
    expect(asBytes(creation.excludeCredentials![0].id))
      .toEqual([255, 0, 251, 16]);
    expect(creation.authenticatorSelection?.userVerification).toBe("required");
    const request = requestOptionsFromJSON(approval);
    expect(asBytes(request.challenge)).toEqual([255, 0, 251, 16]);
    expect(asBytes(request.allowCredentials![0].id))
      .toEqual([0, 1]);
    expect(request.userVerification).toBe("required");
  });

  it("serializes real browser-shaped registration data without approving anything", async () => {
    const create = vi.fn().mockResolvedValue({
      id: "owner-id",
      rawId: bytes(0, 1),
      type: "public-key",
      response: {
        clientDataJSON: bytes(1, 2),
        attestationObject: bytes(3, 4),
        getTransports: () => ["internal"],
      },
      getClientExtensionResults: () => ({}),
    });
    const wire = await registerOwnerPasskey(
      enrollment,
      { create } as unknown as Pick<CredentialsContainer, "create">,
    );
    expect(create).toHaveBeenCalledOnce();
    expect(wire).toEqual({
      id: "owner-id",
      rawId: "AAE",
      type: "public-key",
      response: {
        clientDataJSON: "AQI",
        attestationObject: "AwQ",
        transports: ["internal"],
      },
      clientExtensionResults: {},
    });
  });

  it("serializes the browser assertion and preserves nullable userHandle", async () => {
    const get = vi.fn().mockResolvedValue({
      id: "owner-id",
      rawId: bytes(0, 1),
      type: "public-key",
      response: {
        clientDataJSON: bytes(1, 2),
        authenticatorData: bytes(3, 4),
        signature: bytes(5, 6),
        userHandle: null,
      },
      getClientExtensionResults: () => ({}),
    });
    const result = await verifyOwnerPasskey(
      approval,
      { get } as unknown as Pick<CredentialsContainer, "get">,
    );
    expect(get).toHaveBeenCalledOnce();
    expect(result.response).toEqual({
      clientDataJSON: "AQI",
      authenticatorData: "AwQ",
      signature: "BQY",
      userHandle: null,
    });
  });

  it("does not accept canceled or non-public-key browser responses", async () => {
    const create = vi.fn().mockResolvedValue(null);
    await expect(registerOwnerPasskey(
      enrollment, { create } as unknown as Pick<CredentialsContainer, "create">,
    )).rejects.toThrow("No WebAuthn credential");
    const get = vi.fn().mockResolvedValue({ type: "password", id: "unsafe" });
    await expect(verifyOwnerPasskey(
      approval, { get } as unknown as Pick<CredentialsContainer, "get">,
    )).rejects.toThrow("No WebAuthn credential");
  });

  it("does not use network endpoints or fabricate server authorizations", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      throw new Error("unexpected network call");
    });
    try {
      const create = vi.fn().mockResolvedValue(null);
      await expect(registerOwnerPasskey(
        enrollment, { create } as unknown as Pick<CredentialsContainer, "create">,
      )).rejects.toThrow();
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
