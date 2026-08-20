import { WEBUI_API_BASE_URL } from '$lib/constants';

export type BrainSession = {
	session_id: string;
	room_name: string;
	livekit_url: string;
	token: string;
	expires_at: number;
};

export type BrainMCPServerSettings = {
	ID: string;
	NAME: string;
	URL: string;
	ALLOWED_TOOLS: string;
	HEADERS: string;
};

export type BrainSettings = {
	NAME: string;
	LIVEKIT_URL: string;
	LIVEKIT_API_KEY: string;
	LIVEKIT_API_SECRET: string;
	LLM_MODEL: string;
	STT_LANGUAGE: string;
	MCP_URL: string;
	MCP_ALLOWED_TOOLS: string;
	MCP_HEADERS: string;
	MCP_SERVERS: BrainMCPServerSettings[];
	INSTRUCTIONS: string;
};

export type MCPToolInfo = {
	name: string;
	description: string;
};

const detail = async (res: Response) => {
	try {
		return (await res.json())?.detail ?? res.statusText;
	} catch {
		return res.statusText;
	}
};

export const createBrainSession = async (token: string): Promise<BrainSession> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/brain/sessions`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}` },
		credentials: 'include'
	});
	if (!res.ok) throw new Error(await detail(res));
	return res.json();
};

export const getBrainSettings = async (token: string): Promise<BrainSettings> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/brain/settings`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (!res.ok) throw new Error(await detail(res));
	return res.json();
};

export const getBrainMCPTools = async (
	token: string,
	url: string,
	headers: string
): Promise<MCPToolInfo[]> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/brain/mcp/tools`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify({ url, headers })
	});
	if (!res.ok) throw new Error(await detail(res));
	return res.json();
};

export const updateBrainSettings = async (
	token: string,
	settings: BrainSettings
): Promise<BrainSettings> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/brain/settings`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify(settings)
	});
	if (!res.ok) throw new Error(await detail(res));
	return res.json();
};
