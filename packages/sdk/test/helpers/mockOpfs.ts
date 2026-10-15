/**
 * Minimal in-memory mock of the async OPFS API surface used by the SDK
 * (getDirectoryHandle / getFileHandle / createWritable / entries / removeEntry).
 * Only for unit tests.
 */

class MockFile {
  constructor(readonly name: string, public data: Uint8Array) {}
}

class MockWritable {
  locked = false;
  constructor(private file: MockFile) {}
  async write(data: Uint8Array): Promise<void> {
    this.file.data = new Uint8Array(data);
  }
  async close(): Promise<void> {}
  async seek(): Promise<void> {}
  async truncate(): Promise<void> {}
  async abort(): Promise<void> {}
  getWriter(): never {
    throw new Error('not implemented');
  }
}

class MockFileHandle {
  constructor(private dir: MockDirHandle, readonly name: string) {}
  readonly kind = 'file' as const;
  async isSameEntry(other: unknown): Promise<boolean> {
    return other === (this as unknown);
  }
  async resolve(): Promise<string[] | null> {
    return null;
  }
  async createWritable(): Promise<MockWritable> {
    const file = this.dir.files.get(this.name)!;
    return new MockWritable(file);
  }
  async getFile(): Promise<File> {
    const file = this.dir.files.get(this.name)!;
    return new File([file.data as BlobPart], file.name);
  }
}

class MockDirHandle {
  readonly kind = 'directory' as const;
  readonly dirs = new Map<string, MockDirHandle>();
  readonly files = new Map<string, MockFile>();

  constructor(readonly name: string) {}

  async getDirectoryHandle(
    name: string,
    opts?: { create?: boolean },
  ): Promise<FileSystemDirectoryHandle> {
    let d = this.dirs.get(name);
    if (!d) {
      if (!opts?.create) throw new Error(`NotFoundError: ${name}`);
      d = new MockDirHandle(name);
      this.dirs.set(name, d);
    }
    return d as unknown as FileSystemDirectoryHandle;
  }

  async getFileHandle(
    name: string,
    opts?: { create?: boolean },
  ): Promise<FileSystemFileHandle> {
    let f = this.files.get(name);
    if (!f) {
      if (!opts?.create) throw new Error(`NotFoundError: ${name}`);
      f = new MockFile(name, new Uint8Array(0));
      this.files.set(name, f);
    }
    return new MockFileHandle(this, name) as unknown as FileSystemFileHandle;
  }

  async removeEntry(name: string): Promise<void> {
    if (!this.dirs.delete(name)) this.files.delete(name);
  }

  async *entries(): AsyncIterableIterator<[string, FileSystemHandle]> {
    for (const [name] of this.files) {
      yield [name, new MockFileHandle(this, name) as unknown as FileSystemHandle];
    }
    for (const [name, d] of this.dirs) yield [name, d as unknown as FileSystemHandle];
  }

  async isSameEntry(other: unknown): Promise<boolean> {
    return other === (this as unknown);
  }

  async resolve(): Promise<string[] | null> {
    return null;
  }

  [Symbol.asyncIterator](): AsyncIterableIterator<[string, FileSystemHandle]> {
    return this.entries();
  }
}

export function mockOpfsRoot(): FileSystemDirectoryHandle {
  return new MockDirHandle('root') as unknown as FileSystemDirectoryHandle;
}

export type { MockDirHandle };
