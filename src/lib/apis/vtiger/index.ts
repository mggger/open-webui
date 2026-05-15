import { WEBUI_API_BASE_URL } from '$lib/constants';

export type VtigerLeadSummary = {
	id: string;
	firstname: string;
	lastname: string;
	company: string;
	designation: string;
	email: string;
	phone: string;
	industry: string;
	city: string;
	country: string;
};

export type VtigerSearchResponse = {
	leads: VtigerLeadSummary[];
	limit: number;
	offset: number;
	has_more: boolean;
};

export type VtigerLead = VtigerLeadSummary & {
	// Vtiger returns a free-form bag; these are the fields we know about.
	description?: string;
	website?: string;
	state?: string;
	mobile?: string;
	[key: string]: unknown;
};

export type VtigerConfig = {
	ENABLE_VTIGER_CRM: boolean;
	VTIGER_BASE_URL: string;
	VTIGER_USERNAME: string;
	VTIGER_ACCESS_KEY_SET: boolean;
	VTIGER_VERIFY_SSL: boolean;
};

const handle = async (res: Response) => {
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw err;
	}
	return res.json();
};

export const searchVtigerLeads = async (
	token: string,
	q: string = '',
	limit: number = 20,
	offset: number = 0
): Promise<VtigerSearchResponse> => {
	const params = new URLSearchParams({
		q,
		limit: String(limit),
		offset: String(offset)
	});
	const res = await fetch(`${WEBUI_API_BASE_URL}/vtiger/leads/search?${params.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	return handle(res);
};

export const getVtigerLead = async (token: string, id: string): Promise<VtigerLead> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/vtiger/leads/${encodeURIComponent(id)}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	return handle(res);
};

export const getVtigerConfig = async (token: string): Promise<VtigerConfig> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/vtiger/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	return handle(res);
};

export const updateVtigerConfig = async (
	token: string,
	form: Partial<{
		ENABLE_VTIGER_CRM: boolean;
		VTIGER_BASE_URL: string;
		VTIGER_USERNAME: string;
		VTIGER_ACCESS_KEY: string;
		VTIGER_VERIFY_SSL: boolean;
	}>
): Promise<VtigerConfig> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/vtiger/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(form)
	});
	return handle(res);
};

export const testVtigerConfig = async (
	token: string
): Promise<{ ok: boolean; error?: string }> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/vtiger/config/test`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			authorization: `Bearer ${token}`
		}
	});
	return handle(res);
};
