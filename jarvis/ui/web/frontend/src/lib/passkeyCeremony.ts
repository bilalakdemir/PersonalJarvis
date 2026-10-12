/** Browser-only WebAuthn wire conversion for AERION's owner passkey ceremony.
 *
 * This helper never authenticates or approves a proposal. The server must
 * generate and persist the challenge, verify the authenticator's origin/RP ID,
 * UV bit and signature, then atomically bind its grant to one exact proposal.
 * Invoke these functions from a direct user gesture, never automatically.
 */

export interface PasskeyDescriptorJSON {
  id: string;
  type: "public-key";
  transports?: AuthenticatorTransport[];
}

export interface PasskeyCreationOptionsJSON
  extends Omit<PublicKeyCredentialCreationOptions, "challenge" | "user" | "excludeCredentials"> {
  challenge: string;
  user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
  excludeCredentials?: PasskeyDescriptorJSON[];
}

export interface PasskeyRequestOptionsJSON
  extends Omit<PublicKeyCredentialRequestOptions, "challenge" | "allowCredentials"> {
  challenge: string;
  allowCredentials?: PasskeyDescriptorJSON[];
}

export type PasskeyCredentialJSON = {
  id: string;
  rawId: string;
  type: "public-key";
  response: {
    clientDataJSON: string;
    attestationObject?: string;
    authenticatorData?: string;
    signature?: string;
    userHandle?: string | null;
    transports?: AuthenticatorTransport[];
  };
  clientExtensionResults: AuthenticationExtensionsClientOutputs;
};

/** Encodes binary WebAuthn values; never treats a challenge as plain UTF-8. */
export function binaryToBase64Url(value: ArrayBuffer | ArrayBufferView): string {
  const bytes = value instanceof ArrayBuffer
    ? new Uint8Array(value)
    : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function base64UrlToBinary(value: string): ArrayBuffer {
  if (!/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) {
    throw new Error("Invalid WebAuthn binary encoding");
  }
  const raw = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
  return bytes.buffer;
}

function descriptorFromJSON(descriptor: PasskeyDescriptorJSON): PublicKeyCredentialDescriptor {
  if (descriptor.type !== "public-key") throw new Error("Invalid credential type");
  return { ...descriptor, id: base64UrlToBinary(descriptor.id) };
}

export function creationOptionsFromJSON(
  json: PasskeyCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
  return {
    ...json,
    challenge: base64UrlToBinary(json.challenge),
    user: { ...json.user, id: base64UrlToBinary(json.user.id) },
    excludeCredentials: json.excludeCredentials?.map(descriptorFromJSON),
  };
}

export function requestOptionsFromJSON(
  json: PasskeyRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
  return {
    ...json,
    challenge: base64UrlToBinary(json.challenge),
    allowCredentials: json.allowCredentials?.map(descriptorFromJSON),
  };
}

function requiredPublicKeyCredential(
  credential: Credential | null,
): PublicKeyCredential {
  if (!credential || credential.type !== "public-key" ||
    !("response" in credential) || !("rawId" in credential)) {
    throw new Error("No WebAuthn credential was returned");
  }
  return credential as PublicKeyCredential;
}

export async function registerOwnerPasskey(
  json: PasskeyCreationOptionsJSON,
  credentials: Pick<CredentialsContainer, "create"> = navigator.credentials,
): Promise<PasskeyCredentialJSON> {
  const credential = requiredPublicKeyCredential(
    await credentials.create({ publicKey: creationOptionsFromJSON(json) }),
  );
  const response = credential.response as AuthenticatorAttestationResponse;
  if (!(response.attestationObject instanceof ArrayBuffer) ||
    !(response.clientDataJSON instanceof ArrayBuffer)) {
    throw new Error("Unexpected passkey enrollment response");
  }
  return {
    id: credential.id,
    rawId: binaryToBase64Url(credential.rawId),
    type: "public-key",
    response: {
      clientDataJSON: binaryToBase64Url(response.clientDataJSON),
      attestationObject: binaryToBase64Url(response.attestationObject),
      transports: response.getTransports?.() as AuthenticatorTransport[] | undefined,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

export async function verifyOwnerPasskey(
  json: PasskeyRequestOptionsJSON,
  credentials: Pick<CredentialsContainer, "get"> = navigator.credentials,
): Promise<PasskeyCredentialJSON> {
  const credential = requiredPublicKeyCredential(
    await credentials.get({ publicKey: requestOptionsFromJSON(json) }),
  );
  const response = credential.response as AuthenticatorAssertionResponse;
  if (!(response.authenticatorData instanceof ArrayBuffer) ||
    !(response.clientDataJSON instanceof ArrayBuffer) ||
    !(response.signature instanceof ArrayBuffer)) {
    throw new Error("Unexpected passkey assertion response");
  }
  return {
    id: credential.id,
    rawId: binaryToBase64Url(credential.rawId),
    type: "public-key",
    response: {
      clientDataJSON: binaryToBase64Url(response.clientDataJSON),
      authenticatorData: binaryToBase64Url(response.authenticatorData),
      signature: binaryToBase64Url(response.signature),
      userHandle: response.userHandle === null ? null : binaryToBase64Url(response.userHandle),
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}
