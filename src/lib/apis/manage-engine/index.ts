import { WEBUI_API_BASE_URL } from '$lib/constants';
export type ManageEngineConfig = {
	MANAGE_ENGINE_ENDPOINT: string;
	MANAGE_ENGINE_API_KEY_SET: boolean;
	MANAGE_ENGINE_VERIFY_SSL: boolean;
	MANAGE_ENGINE_TIMEZONE: string;
	MANAGE_ENGINE_ADMIN_ONLY: boolean;
};
async function request(token: string, path: string, body?: object) {
	const res = await fetch(`${WEBUI_API_BASE_URL}/manage_engine/${path}`, {
		method: path === 'config' ? 'GET' : 'POST',
		headers: { authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		...(body ? { body: JSON.stringify(body) } : {})
	});
	const data = await res.json();
	if (!res.ok)
		throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
	return data;
}
export const getManageEngineConfig = (token: string): Promise<ManageEngineConfig> =>
	request(token, 'config');
export const updateManageEngineConfig = (
	token: string,
	body: Partial<ManageEngineConfig> & { MANAGE_ENGINE_API_KEY?: string }
): Promise<ManageEngineConfig> => request(token, 'config/update', body);
export const testManageEngineConfig = (
	token: string,
	body?: Partial<ManageEngineConfig> & { MANAGE_ENGINE_API_KEY?: string }
): Promise<{ ok: boolean; error?: string }> => request(token, 'config/test', body);
