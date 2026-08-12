// frontend/src/components/ui/UserSelector.tsx

// frontend/src/components/ui/UserSelector.tsx
import React, { useMemo, useState } from 'react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Check, ChevronsUpDown, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Badge } from './badge';

// --- Interfaces ---
export interface UserSquadInfo {
  id: number;
  name: string;
}

export interface SelectableUser {
  id: number;
  // Contato no L1. NULO enquanto o usuário do Entra não casar com um contato
  // — nesse estado ele não pode ser responsável por tarefa.
  external_id: number | null;
  name: string;
  squads: UserSquadInfo[];
  email?: string | null;
}

interface UserSelectorProps {
  users: SelectableUser[];
  // O valor selecionado é o `external_id` do usuário como string, ou null se nada for selecionado
  value: string | null;
  // Callback para notificar a mudança de valor
  onChange: (value: string | null) => void;
  // Permite filtrar os usuários mostrados com base nos IDs dos squads
  filterBySquadIds?: number[];
  disabled?: boolean;
  placeholder?: string;
  // Quando true, mostra "nome · email" no trigger e na lista. Util pra
  // desambiguar usuarios com nomes parecidos (ex.: catalogo do L1).
  showEmail?: boolean;
}

const UserSelector = ({
  users,
  value,
  onChange,
  filterBySquadIds = [],
  disabled = false,
  placeholder = 'Selecione um responsável...',
  showEmail = false,
}: UserSelectorProps) => {
  const [open, setOpen] = useState(false);

  // Deriva o usuário selecionado a partir do `value` (external_id)
  const selectedUser = useMemo(() => {
    return users.find(u => String(u.external_id) === value) || null;
  }, [value, users]);

  // Filtra os usuários com base na busca e nos Squads
  const filteredUsers = useMemo(() => {
    if (filterBySquadIds.length === 0) {
      return users;
    }
    return users.filter(user =>
      user.squads.some(squad => filterBySquadIds.includes(squad.id))
    );
  }, [users, filterBySquadIds]);

  // Sem contato no Legal One a pessoa NÃO pode ser responsável por tarefa: o
  // payload do L1 exige `contact.id`. Deixá-la na lista só produzia falha
  // silenciosa. Ela usa o Flow normalmente — só não aparece aqui.
  const usuariosSelecionaveis = useMemo(
    () => filteredUsers.filter((u) => u.external_id != null),
    [filteredUsers],
  );
  const ocultosSemContato = filteredUsers.length - usuariosSelecionaveis.length;

  // Recebe o USUÁRIO, não o nome. Resolver por nome quebrava em silêncio
  // quando duas pessoas tinham o mesmo nome: o `find` devolvia a primeira, que
  // podia ser outra pessoa — ou um registro sem contato no L1, e aí o valor
  // virava "null", o pai fazia parseInt("null") = NaN e o campo voltava EM
  // BRANCO, sem erro nenhum (caso da Ana Carolina, 07/08/2026).
  const handleSelect = (selected: SelectableUser) => {
    const novo = String(selected.external_id);
    onChange(novo === value ? null : novo);
    setOpen(false);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation(); // Impede que o Popover seja aberto
    onChange(null);
  };

  return (
    <div className="relative">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="w-full justify-between"
            disabled={disabled}
          >
            {selectedUser ? (
              <span className="truncate">
                {selectedUser.name}
                {showEmail && selectedUser.email && (
                  <span className="ml-1 text-muted-foreground">
                    · {selectedUser.email}
                  </span>
                )}
              </span>
            ) : (
              <span className="text-muted-foreground">{placeholder}</span>
            )}
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        {selectedUser && !disabled && (
          <Button
            variant="ghost"
            size="icon"
            onClick={handleClear}
            className="absolute right-10 top-1/2 -translate-y-1/2 h-6 w-6"
            aria-label="Limpar seleção"
          >
            <X className="h-4 w-4 text-muted-foreground" />
          </Button>
        )}
        <PopoverContent className="w-[--radix-popover-trigger-width] p-0">
          <Command>
            <CommandInput placeholder="Buscar usuário..." />
            <CommandList>
              <CommandEmpty>Nenhum usuário encontrado.</CommandEmpty>
              {ocultosSemContato > 0 && (
                <div className="border-b px-3 py-2 text-[11px] text-muted-foreground">
                  {ocultosSemContato} pessoa(s) fora da lista por não terem
                  cadastro de contato no Legal One — sem isso o L1 não aceita
                  vinculá-las como responsável.
                </div>
              )}
              <CommandGroup>
                {usuariosSelecionaveis.map(user => (
                  <CommandItem
                    key={user.external_id ?? `sem-contato-${user.id}`}
                    // Concatena email no value pra que o cmdk filtre por
                    // nome OU email (busca livre por qualquer pedaco).
                    value={showEmail && user.email ? `${user.name} ${user.email}` : user.name}
                    onSelect={() => handleSelect(user)}
                  >
                    <Check
                      className={cn(
                        'mr-2 h-4 w-4',
                        value === String(user.external_id)
                          ? 'opacity-100'
                          : 'opacity-0'
                      )}
                    />
                    <div className="flex flex-col">
                      <span>
                        {user.name}
                        {showEmail && user.email && (
                          <span className="ml-1 text-muted-foreground">
                            · {user.email}
                          </span>
                        )}
                      </span>
                      <div className="flex flex-wrap gap-1 text-xs">
                        {user.squads.map(squad => (
                          <Badge key={squad.id} variant="secondary">
                            {squad.name}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default UserSelector;