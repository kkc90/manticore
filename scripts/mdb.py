from cmd import Cmd
from os import listdir
import re

from manticore.core.plugin import Plugin
from manticore.ethereum import ManticoreEVM, evm, Operators, ABI
from manticore.core.state import TerminateState, Breakpoint
import pyevmasm as EVMAsm


class ManticoreDebugger(Cmd):
    intro = ''' A eth debugger '''
    prompt = 'mdb> '
    def __init__(self):
        super().__init__()
        self.m = ManticoreEVM(procs=1)
        # Fixme make this a Plugin
        self.m._executor.subscribe('will_evm_execute_instruction', self.will_evm_execute_instruction_callback)
        self.m._executor.subscribe('will_terminate_state', self._terminate_state_callback)
        self.user_account = self.m.create_account(balance=1000, name='user_account')
        self.owner_account = self.m.create_account(balance=1000, name='owner_account')
        self.current_account = self.user_account
        self.breakpoints = set() # (address, evmoffset)
        self.stepping = False

    def _terminate_state_callback(self, state, state_id, e):
        '''self.m._executor.initial_state = state
        self.m._executor._initial_state = state
        self.m._initial_state = state'''
        if isinstance(e, Breakpoint):
            assert (len(self.m._running_state_ids)<=1)
            print ("AT TERMINATE STATE")
            # save the state to secondary storage
            #new_state_id = self.m._executor._workspace.save_state(state)

            #with self.m.locked_context('ethereum') as context:
                    # Keep it on a private list
            #        context['_saved_states'].add(new_state_id)

            #self.m.save(state)  # Add to running states

            '''with self.m.locked_context('ethereum') as context:
                new_state_id = self.m._executor._workspace.save_state(state)
                context['_saved_states'] = set((new_state_id,))
                context['_final_states'] = set()'''

    def will_evm_execute_instruction_callback(self, state, instruction, arguments):
        ''' Internal breakpoint callback '''
        if not self.stepping:
            world = state.platform
            mnemonic = instruction.semantics
            current_vm = world.current_vm
            if (current_vm.address,  current_vm.pc) in self.breakpoints:
                print ("breakpoint", current_vm)
                #self.m._executor.shutdown()
                raise Breakpoint()

    def _get_account_address(self, inp):
        state = self._get_state()
        blockchain = state.platform
        try:
            return self.m.get_account(inp).address
        except:
            return self.m.get_account(int(inp, 0)).address

    def _get_state(self):
        for state in self.m.all_states:
            return state

    def _print_last_tx_result(self):
        state = self._get_state()
        blockchain = state.platform
        if blockchain.last_transaction.result == 'RETURN':
            metadata = self.m.get_metadata(blockchain.last_transaction.address)
            return_data = state.solve_one(blockchain.last_transaction.return_data)
            function_id = bytes(blockchain.last_transaction.data[:4])  # hope there is enough data
            signature = metadata.get_func_signature(function_id)
            function_name = metadata.get_func_name(function_id)
            ret_types = metadata.get_func_return_types(function_id)
            return_values = ABI.deserialize(ret_types, return_data)  # function return
            print ("return: ", repr(return_values))


    # Commands 
    def do_continue(self, inp):
        self.m.run()
        self._print_last_tx_result()

    def do_step(self, inp):
        ''' step 1 instruction '''
        for state in self.m.all_states:
            blockchain = state.platform
            if blockchain.current_transaction:
                try:
                    # notify siblings we have a state to play with
                    self.stepping=True
                    blockchain.execute()
                    print (blockchain.current_vm)

                except Exception as e:
                    print (e)
                    self._print_last_tx_result()
                    return
                finally:
                    self.stepping = False
    
    def do_where(self, inp):
        ''' Print the transaction call and evm call stack '''
        try:
            state = self._get_state()
            blockchain = state.platform
            address, offset = blockchain.current_vm.address, blockchain.current_vm.pc
            metadata = self.m.get_metadata(blockchain.last_transaction.address)
            print ("Your are at contract", self.m.account_name(address))
            print ("PC:", offset)
            print (metadata.get_source_for(offset, runtime=True))
        except Exception as e:
            print ("limbo", e)

    def do_show(self, inp):
        ''' Show current world state '''
        assert self.m.count_states() == 1
        state = self._get_state()
        blockchain = state.platform
        for account_address in blockchain.accounts:
            print ("Address: %s (0x%x)" % (self.m.account_name(account_address), account_address))
            print ("Balance: ", blockchain.get_balance(account_address))
            print ("Storage: - not implemented -\n")
        print ("Breakpoints:", self.breakpoints)
        return False

    def do_pdb(self, inp):
        import pdb; pdb.set_trace()

    def do_breakpoint(self, inp):
        m = re.search(r'(\w+) (.+)', inp)
        contract = None
        if m:
            contract = self._get_account_address(m.group(1))
            if not contract:
                return
            offset = int(m.group(2), 0)
        if contract is not None:
            self.breakpoints.add((contract, offset))
            print ("breakpoint set")
        else:
            print ("error")

    def do_exit(self, inp):
        ''' Exit debugger '''
        print("Bye")
        return True 

    def do_whoami(self, inp):
        """ Print current user """
        print (self.m.account_name(self.current_account))

    def do_su(self, inp):
        """ change current user """
        if not inp:
            inp = 'owner_account'
        state = self._get_state()
        blockchain = state.platform
        for account_address in blockchain.accounts:
            account_name = self.m.account_name(account_address)
            if account_address == inp or account_name == inp:
                self.current_account = account_address
                break
        return False

    def do_create(self, inp):
        ''' Create contract 
            create somecontract.sol ContractName2 [(args ...)]
            only int args implemented
        '''

        m = re.search(r'(\w+)(\w+)\((.*)\)', inp)
        if m:
            filename = m.group(1)
            contract_name = m.group(2)
            args = map(lambda x: int(x, 0), m.group(3).split(','))
            print ("args ignored")
        else:
            m = re.search(r'([a-zA-Z_.]+) (\w+)', inp)
            if m:
                filename = m.group(1)
                contract_name = m.group(2)
                args = []
            else:
                print ("error")
                return False

        with open(filename) as source_code:
            contract_account = self.m.solidity_create_contract(source_code, owner=self.current_account, name=contract_name)
        print (contract_account, contract_name)

    def do_call(self, inp):
        ''' make a tx 
            call pretty_name function_name 3876 87683 
        '''
        state = self._get_state()
        blockchain = state.platform
        if blockchain.current_transaction:
            print ("already an ongoing tx. continue,  step")

        m = re.search(r'(\w+) (\w+)(.*)', inp)
        if m:
            contract_name = m.group(1)
            function_name = m.group(2)
            args = map(lambda x: int(x, 0), m.group(3).strip().split(' '))
        else:
            print ("error", m)
            return False

        account_address = self._get_account_address(contract_name)
        account = self.m.get_account(account_address)
        
        try:
            result = getattr(account, function_name)(*args)
            self._print_last_tx_result()
        except Exception as e:
            import traceback
            traceback.print_exc()
            import pdb; pdb.set_trace()
            print ("EEE", e)
            pass
        return False

    def do_list(self, inp):
        m = re.search(r'(\w+)', inp)
        if m:
            contract_name = m.group(1)
        else:
            print ("error", m)
            return False

        target_account_address = self._get_account_address(contract_name)
        md = self.m.get_metadata(target_account_address)
        if md:
            bytecode = md.runtime_bytecode
        else:    
            blockchain = self._get_state().platform
            bytecode = blockchain.get_code(account_address)
        
        for i in EVMAsm.disassemble_all(bytecode):
            print (hex(i.pc), i)

    # Complete logic
    def _complete_account(self, stem):
        blockchain = self._get_state().platform

        accounts = []
        for account_address in blockchain.accounts:
            if not blockchain.get_code(account_address):
                continue
            account_name = self.m.account_name(account_address)
            if account_name.startswith(stem):
                accounts.append(account_name)
            else:
                accounts.append(hex(account_address))
        return accounts

    def _complete_function_names(self, contract_name, stem):
        state = self._get_state()
        blockchain = state.platform
        target_account_address = self.m.get_account(contract_name)
        md = self.m.get_metadata(target_account_address)
        result_funcs = []

        if md is not None:
            for func_name in  md.functions:
                try:
                    func_name = func_name.split('(')[0]
                except:
                    pass
                if func_name.startswith(stem):
                    result_funcs.append(func_name)
        return result_funcs

    def complete_create(self, text, line, begidx, endidx):
        count = len(re.findall("[a-zA-Z_.]+", line[:begidx]))
        if count == 1:
            if line[begidx:endidx]:
                return [i for i in listdir() if i.startswith(line[begidx:endidx])]
            else:
                return [i for i in listdir() if i.endswith('.sol')]
        else:
            filename = re.findall("([\w+.]+)", line)[1]
            with open(filename) as f:
                source_code = f.read()
                return re.findall("contract +(\w+)", source_code)
        return []

    def complete_call(self, text, line, begidx, endidx):
        try:
            blockchain = self._get_state().platform
            count = len(re.findall("[a-zA-Z_.]+", line[:begidx]))
            if count == 1:
                return self._complete_account(line[begidx:endidx])
            elif count ==  2:
                contract_name = re.findall("([\w+.]+)", line)[1]
                return self._complete_function_names(contract_name, line[begidx:endidx])
            return []
        except Exception as e:
            pass

    def complete_breakpoint(self, text, line, begidx, endidx):
        count = len(re.findall("[a-zA-Z_.]+", line[:begidx]))
        if count == 1:
            return self._complete_account(line[begidx:endidx])
        elif count ==  2:
            #line number
            return []

    def complete_list(self, text, line, begidx, endidx):
        count = len(re.findall("[a-zA-Z_.]+", line[:begidx]))
        if count == 1:
            return self._complete_account(line[begidx:endidx])



ManticoreDebugger().cmdloop()

