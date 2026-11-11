import { WEBUI_API_BASE_URL } from '$lib/constants';

export type FileSearchConfig = {
	configured: boolean;
	server: string;
	share: string;
	root: string;
	username: string;
	password_configured: boolean;
	default_directory: string;
};

export type FileSearchDirectory = {
	name: string;
	path: string;
};

const request = async <T>(token: string, path: string, options: RequestInit = {}): Promise<T> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/file-search${path}`, {
		...options,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	});
	if (!response.ok) {
		const error = await response.json().catch(() => ({ detail: response.statusText }));
		throw error.detail ?? error;
	}
	return response.json();
};

export const getFileSearchConfig = (token: string): Promise<FileSearchConfig> =>
	request(token, '/config');

export const updateFileSearchConfig = (
	token: string,
	payload: { username: string; password?: string; default_directory: string }
): Promise<FileSearchConfig> =>
	request(token, '/config', {
		method: 'PUT',
		body: JSON.stringify(payload)
	});

export const testFileSearchConnection = (
	token: string,
	payload: { username: string; password?: string }
): Promise<{ success: boolean }> =>
	request(token, '/test-connection', {
		method: 'POST',
		body: JSON.stringify(payload)
	});

export const deleteFileSearchConfig = (token: string): Promise<{ deleted: boolean }> =>
	request(token, '/config', { method: 'DELETE' });

export const getFileSearchDirectories = (
	token: string,
	path = ''
): Promise<{
	current: string;
	parent: string;
	directories: FileSearchDirectory[];
	default_directory: string;
}> => request(token, `/directories?path=${encodeURIComponent(path)}`);
