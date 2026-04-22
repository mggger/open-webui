import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ConversationAgent = {
	id: string;
	user_id: string;
	name: string;
	description?: string | null;
	system_prompt?: string | null;
	model_id?: string | null;
	voice_config?: object | null;
	meta?: object | null;
	access_control?: object | null;
	created_at: number;
	updated_at: number;
};

export type ConversationAgentForm = {
	name: string;
	description?: string | null;
	system_prompt?: string | null;
	model_id?: string | null;
	voice_config?: object | null;
	meta?: object | null;
	access_control?: object | null;
};

export const getConversationAgents = async (token: string): Promise<ConversationAgent[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/conversation-agents/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}
	return res ?? [];
};

export const createConversationAgent = async (
	token: string,
	form: ConversationAgentForm
): Promise<ConversationAgent> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/conversation-agents/create`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(form)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}
	return res;
};

export const getConversationAgentById = async (
	token: string,
	id: string
): Promise<ConversationAgent> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/conversation-agents/${id}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}
	return res;
};

export const updateConversationAgentById = async (
	token: string,
	id: string,
	form: ConversationAgentForm
): Promise<ConversationAgent> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/conversation-agents/${id}/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(form)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}
	return res;
};

export const deleteConversationAgentById = async (token: string, id: string): Promise<boolean> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/conversation-agents/${id}/delete`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}
	return res;
};
