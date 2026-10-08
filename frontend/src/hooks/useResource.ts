import { useEffect, useState } from 'react';

export type ResourceState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: Error };

export function useResource<T>(
  loader: (() => Promise<T>) | null,
  dependencies: readonly unknown[] = [],
) {
  const [state, setState] = useState<ResourceState<T>>({ status: 'idle' });
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!loader) {
      setState({ status: 'idle' });
      return;
    }
    let active = true;
    setState({ status: 'loading' });
    loader()
      .then((data) => {
        if (active) setState({ status: 'success', data });
      })
      .catch((error: unknown) => {
        if (active) {
          setState({
            status: 'error',
            error: error instanceof Error ? error : new Error('Request failed.'),
          });
        }
      });
    return () => {
      active = false;
    };
  }, [...dependencies, loader, refreshKey]);

  return { state, refresh: () => setRefreshKey((value) => value + 1) };
}